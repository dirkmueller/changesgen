#!/usr/bin/python3

"""expand indirect dependencies"""

import argparse
import functools
import itertools
import logging as LOG
import json
import time
import urllib
import re
import os
import fcntl
import struct
import tempfile
from pathlib import Path

import osc.conf
from lxml import etree as ET
from osc.util.cpio import CpioHdr
from osc.core import http_GET, makeurl

API_URL = 'https://api.suse.de'

import rpm

BIN2PKG = {}

be_strict_with_errors = False

rpm_ts = rpm.TransactionSet()


class RepoMirror:
    cpio_struct = struct.Struct('6s8s8s8s8s8s8s8s8s8s8s8s8s8s')
    cpio_name_re = re.compile('^([^/]+)-([0-9a-f]{32})$')

    def __init__(
        self, apiurl: str, nameignore: str = '-debug(info|source|info-32bit).rpm$'
    ):
        """
        Class to mirror RPM headers of all binaries in a repo on OBS (full tree).
        Debug packages are ignored by default, see the nameignore parameter.
        """
        self.apiurl = apiurl
        self.nameignorere = re.compile(nameignore)

    def extract_cpio_stream(self, destdir: str, stream):
        while True:
            hdrtuples = self.cpio_struct.unpack(stream.read(self.cpio_struct.size))
            # Read and parse the CPIO header
            if hdrtuples[0] != b'070701':
                raise NotImplementedError(f'CPIO format {hdrtuples[0]} not implemented')

            # The new-ascii format has padding for 4 byte alignment
            def align():
                stream.read((4 - (stream.tell() % 4)) % 4)

            hdr = CpioHdr(*hdrtuples)
            hdr.filename = stream.read(hdr.namesize - 1).decode('ascii')
            stream.read(1)  # Skip terminator
            align()

            binarymatch = self.cpio_name_re.match(hdr.filename)
            if hdr.filename == '.errors':
                content = stream.read(hdr.filesize)
                raise RuntimeError('Download has errors: ' + content.decode('ascii'))
            elif binarymatch:
                name = binarymatch.group(1)
                md5 = binarymatch.group(2)
                destpath = os.path.join(destdir, f'{md5}-{name}.rpm')
                with tempfile.NamedTemporaryFile(mode='wb', dir=destdir) as tmpfile:
                    # Probably not big enough to need chunking
                    tmpfile.write(stream.read(hdr.filesize))
                    os.link(tmpfile.name, destpath)
                    # Would be nice to use O_TMPFILE + link here, but python passes
                    # O_EXCL which breaks that.
                    # os.link(f'/proc/self/fd/{tmpfile.fileno()}', destpath)

                align()
            elif hdr.filename == 'TRAILER!!!':
                if stream.read(1):
                    raise RuntimeError('Expected end of CPIO')
                break
            else:
                raise NotImplementedError(f'Unhandled file {hdr.filename} in archive')

    def _mirror(self, destdir: str, prj: str, repo: str, arch: str) -> None:
        "Using the _repositories endpoint, download all RPM headers into destdir."
        LOG.info(f'Mirroring {prj}/{repo}/{arch}')
        pkglistxml = http_GET(
            makeurl(
                self.apiurl,
                ['build', prj, repo, arch, '_repository'],
                query={'view': 'binaryversions', 'nometa': 1},
            )
        )
        root = ET.parse(pkglistxml).getroot()
        remotebins: dict[str, str] = {}
        for binary in root.findall('binary'):
            name = binary.get('name')
            if name.endswith('.rpm') and not self.nameignorere.search(name):
                hdrmd5 = binary.get('hdrmd5')
                remotebins[f'{hdrmd5}-{name}'] = name[:-4]

        to_delete: list[str] = []
        for filename in os.listdir(destdir):
            if not filename.endswith('.rpm'):
                continue

            if filename in remotebins:
                del remotebins[filename]  # Already downloaded
            else:
                to_delete.append(os.path.join(destdir, filename))

        if to_delete:
            LOG.info(f'Deleting {len(to_delete)} old packages')
            for path in to_delete:
                os.unlink(path)

        if remotebins:
            LOG.info(f'Downloading {len(remotebins)} new packages')
            binaries = remotebins.values()

            # Download in batches of 50
            for chunk in range(0, len(binaries), 50):
                query = 'view=cpioheaders'
                for binary in itertools.islice(binaries, chunk, chunk + 50):
                    query += '&binary=' + urllib.parse.quote_plus(binary)

                req = http_GET(
                    makeurl(
                        self.apiurl,
                        ['build', prj, repo, arch, '_repository'],
                        query=query,
                    )
                )
                self.extract_cpio_stream(destdir, req)

    def mirror(self, destdir: str, prj: str, repo: str, arch: str) -> None:
        "Creates destdir and locks destdir/.lock before mirroring."
        os.makedirs(destdir, exist_ok=True)

        with open(os.path.join(destdir, '.lock'), 'w') as lockfile:
            try:
                fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except IOError:
                logger.info(destdir + 'is locked, waiting... ')
                fcntl.flock(lockfile, fcntl.LOCK_EX)
                logger.info('Lock acquired!')

            return self._mirror(destdir, prj, repo, arch)


@functools.cache
def get_project_dependencies(project, repository, arch):
    bdep = http_GET(
        makeurl(
            API_URL,
            ['build', project, repository, arch, '_builddepinfo'],
            query={'view': 'pkgnames'},
        )
    )
    root = ET.parse(bdep).getroot()
    r = {}
    global BIN2PKG
    for pkg in root.findall('package'):
        pkgdeps = [pkg for pkg in pkg.findall('pkgdep')]
        r[pkg] = pkgdeps
        for sub in pkg.findall('subpkg'):
            BIN2PKG[(project, sub.text)] = pkg.attrib['name']
    return r


@functools.cache
def mirror_repository(project, repository):
    basedir = Path().home() / '.cache' / 'openSUSE-release-tools' / 'repository-meta'

    repo_mirror = RepoMirror(API_URL, nameignore='-debug(info|source|info-32bit).rpm$')
    repo_mirror.mirror(
        str(basedir / project / repository), project, repository, 'x86_64'
    )

    global BIN2PKG

    for pkg in Path.iterdir(basedir / project / repository):
        if '-' not in pkg.name:
            continue

        global rpm_ts
        try:
            fdno = os.open(basedir / project / repository / pkg.name, os.O_RDONLY)

            rpm_ts.setVSFlags(~(rpm.RPMVSF_NEEDPAYLOAD))
            try:
                hdr = rpm_ts.hdrFromFdno(fdno)
            except rpm.error as e:
                LOG.warning(f'{pkg.name}: {e}')
                hdr = None
            if not isinstance(hdr, rpm.hdr):
                hdr = None

        finally:
            rpm_ts.setVSFlags(0)
            os.close(fdno)

        package_name = '%s' % (hdr[rpm.RPMTAG_NAME],)
        package_date = hdr[rpm.RPMTAG_BUILDTIME]
        sources = hdr[rpm.RPMTAG_DISTURL]
        disturl = urllib.parse.urlparse(hdr[rpm.RPMTAG_DISTURL])

        src_revision, _, src_package = Path(disturl.path).name.partition('-')
        src_package = src_package.partition(':')[0]  # strip multibuild flavor
        src_project = Path(disturl.path).parent.parent.name

        BIN2PKG[(src_project, package_name)] = src_package
        BIN2PKG[(project, package_name)] = src_package

        # LOG.info(f'Package: {package_name}, Date: {package_date}, src_package: {src_package} src_project {src_project}')


def expand_proj_deps(project, repository, arch, pkgname):
    expanded_prjpkg = set()
    work_prjpkgs = set()

    work_prjpkgs.add((project, repository, pkgname))
    project_deps = {}
    while work_prjpkgs:
        prjpkg = work_prjpkgs.pop()
        if prjpkg in expanded_prjpkg:
            continue
        expanded_prjpkg.add(prjpkg)

        current_prj, current_rep, current_pkg = prjpkg
        LOG.info(f'fetching buildenv for {current_prj}/{current_rep}/{current_pkg}')
        time.sleep(0.2)
        buildenv = http_GET(
            makeurl(
                API_URL,
                ['build', current_prj, current_rep, arch, current_pkg, '_buildenv'],
            )
        )

        for binary in ET.parse(buildenv).getroot().findall('bdep'):
            if binary.attrib['project'] not in project_deps:
                project_deps[binary.attrib['project']] = get_project_dependencies(
                    binary.attrib['project'], binary.attrib['repository'], arch
                )
            # find package name

            deppkg = BIN2PKG.get((binary.attrib['project'], binary.attrib['name']))
            if deppkg is None:
                LOG.warning(
                    f"No package found for {binary.attrib['project']}/{binary.attrib['repository']}/{binary.attrib['name']}"
                )
                mirror_repository(binary.attrib['project'], binary.attrib['repository'])
                """
                try:
                    fileinfo = http_GET(
                        makeurl(
                            API_URL,
                            [
                                'build',
                                binary.attrib['project'],
                                binary.attrib['repository'],
                                arch,
                                '_repository',
                                f"{binary.attrib['name']}.rpm",
                            ],
                            query={'view': 'fileinfo'},
                        )
                    )
                    for source in ET.parse(fileinfo).getroot().findall('source'):
                        BIN2PKG[(binary.attrib['project'], binary.attrib['name'])] = (
                            deppkg
                        ) = source.text

                except urllib.error.HTTPError as e:
                    if not be_strict_with_errors and e.status == 404:
                        LOG.warning(f"Used a binary {binary.attrib['name']} for build of {current_pkg} that no longer exists!")
                        pass
                    else:
                        raise
                """
            if (
                deppkg is not None
                and (binary.attrib['project'], binary.attrib['repository'], deppkg)
                not in expanded_prjpkg
            ):
                work_prjpkgs.add(
                    (binary.attrib['project'], binary.attrib['repository'], deppkg)
                )

    return expanded_prjpkg


def main():
    parser = argparse.ArgumentParser(description='Expand indirect dependencies')
    parser.add_argument('-d', '--debug', action='store_true')
    parser.add_argument('repodir', nargs='+', help='path to the source DVD repo')

    args = parser.parse_args()

    osc.conf.get_config()

    LOG.basicConfig(
        level=LOG.DEBUG if args.debug else LOG.INFO,
        datefmt='%y-%m-%d %H:%M:%S',
        format='%(asctime)s %(message)s',
    )

    r = expand_proj_deps('SUSE:SLE-15-SP5:GA', 'standard', 'x86_64', 'alsa')
    # r = expand_proj_deps('SUSE:ALP:Source:Standard:Core:1.0:Build', 'standard', 'x86_64', 'alsa')
    print(json.dumps(r))


if __name__ == '__main__':
    main()
