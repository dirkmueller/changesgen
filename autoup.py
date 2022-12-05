#!/usr/bin/python3

import argparse
import os
import glob
import sh
import logging as LOG
import secrets
import string
import requests
import urllib.parse


def parse_from_spec_file():
    primary_spec = sorted(glob.glob('*.spec'), key=len)

    pkg_info = {}

    if not len(primary_spec):
        return pkg_info

    for line in open(primary_spec[0]):
        if line.partition(' ')[0] in ('%description', '%package'):
            break

        line_keyword = line.partition(':')[0].lower()

        if line_keyword in ('name', 'version'):
            pkg_info[line_keyword] = line.strip().split(' ')[-1]

        if ((line_keyword in ('source', 'source0', 'url')) and 'github.com' in line):
            gh_url = line.strip().split(' ')[-1]

            for k in pkg_info:
                gh_url = gh_url.replace('%{' + k + '}', pkg_info[k])

            # normalize
            o = urllib.parse.urlparse(gh_url)
            pkg_info['github_project'] = '/'.join(o.path.split('/')[1:3])

    return pkg_info


def repology_get_project_candidates():

    random_letter = secrets.choice(string.ascii_lowercase)
    random_letter = 'p'
    resp = requests.get(
        f"https://repology.org/api/v1/projects/{random_letter}/?inrepo=opensuse_tumbleweed&outdated=1&family_newest=1-"
    )
    pkgs = {}
    if resp.status_code == 200:
        resp = resp.json()
        for upstream_package in resp:
            package = None

            # determine openSUSE package name
            for repo in resp[upstream_package]:
                if repo['repo'] == 'opensuse_tumbleweed':
                    package = repo['srcname']
                    break

            if not package:
                # TODO
                continue

            pkgs[package] = {}
            for repo in resp[upstream_package]:
                if repo['repo'] == 'opensuse_tumbleweed':
                    pkgs[package]['oldv'] = repo['version']
                if repo['status'] == 'newest':
                    pkgs[package]['newv'] = repo['version']

            if 'oldv' not in pkgs[package] or 'newv' not in pkgs[package]:
                pkgs.pop(package)

    return pkgs


def test_for_package_checkout(name):
    sh.cd(os.path.expanduser("~/src/os/Factory"))
    assert "/" not in name
    sh.rm("-rf", name)
    try:
        sh.osc.co(name)
    except sh.ErrorReturnCode_1:
        return False
    else:
        if os.path.exists(f"{name}/_service") or os.path.exists(f"{name}/_multibuild"):
            # TODO handle services as well
            sh.rm('-rf', name)
            return False

    sh.cd(name)
    return True


def test_for_package_version_update(pname, oldv, newv):
    if test_for_package_checkout(pname):
        package_information = parse_from_spec_file()
        primary_spec = sorted(glob.glob('*.spec'), key=len)

        if len(primary_spec) == 1:
            primary_spec = primary_spec[0]

            if package_information['version'] in (oldv, ):
                sh.sed(
                    '-i', '-r', '-e',
                    f"s,^Version: *{oldv},Version:        {newv},",
                    primary_spec)
                try:
                    sh.Command('/usr/lib/obs/service/download_files')('--outdir', os.getcwd())
                    # sh.osc.service.disabledrun.download_files()
                except sh.ErrorReturnCode_1:
                    print(".. downloading new sources failed")
                    sh.cd('..')
                else:
                    for fname in glob.glob(f"*{newv}*"):
                        oldname = fname.replace(newv, oldv)
                        if os.path.exists(oldname):
                            os.remove(oldname)

                    try:
                        sh.osc.build(
                            '--noservice', '--vm-type=kvm', '--clean',
                            'standard', 'x86_64', primary_spec)
                    except sh.ErrorReturnCode_1:
                        print(".. build failed")
                        sh.cd('..')
                    else:
                        print("!! osc build Success!")
                        return True
    sh.rm('-rf', pname)
    return False


def main():
    # LOG.basicConfig(level=LOG.DEBUG)

    parse = argparse.ArgumentParser(description='Test for version updates')
    parse.add_argument('letter', metavar='letter', type=str, help='starting name to try')

    pkgs = repology_get_project_candidates()

    stat_tested = stat_tested_success = 0

    while len(pkgs):
        pname = secrets.choice([p for p in pkgs])
        pkg = pkgs.pop(pname)
        print(f"[{stat_tested_success}/{stat_tested}] Testing {pname}: {pkg['oldv']} -> {pkg['newv']} (remaining {len(pkgs)})")
        stat_tested += 1

        if test_for_package_version_update(pname, pkg['oldv'], pkg['newv']):
            stat_tested_success += 1


main()
