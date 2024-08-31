#!/usr/bin/python3
"""Generate *.changes release notes entry for version updates. Useful for openSUSE packages.
Copyright (C) 2022 Dirk Müller, SUSE LLC

This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program; if not, write to the Free Software
Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

SPDX-License-Identifier: GPL-2.0-or-later
"""

import argparse
import configparser
import glob
import logging as LOG
import os
import re
import tarfile
import textwrap
import time
import urllib.parse
from subprocess import PIPE, STDOUT, Popen

import packaging.version as pv
import requests
from bs4 import BeautifulSoup
from docutils.core import publish_parts

NEWRELEASES_API_KEY = None


def parse_from_spec_file(path):
    """Parse the spec file and return a dictionary with relevant parts of information, like
    upstream home url, version numbers etc"""
    primary_spec = sorted(glob.glob(os.path.join(path, '*.spec')), key=len)

    pkg_info = {}

    if not primary_spec:
        return pkg_info

    parsed_spec = Popen(
        ('rpmspec', '-P', primary_spec[0]), stdout=PIPE, stderr=STDOUT, text=True
    )
    specfile = parsed_spec.communicate()[0].split('\n')

    # did spec preprocessing fail?
    if parsed_spec.returncode > 0:
        specfile = open(primary_spec[0], 'rt').readlines()

    for line in specfile:
        line = line.strip()

        if line.partition(' ')[0] in ('%description', '%package'):
            break

        rpmtag = line.partition(':')[0].lower()

        if rpmtag in ('source', 'source0'):
            pkg_info['source'] = line.strip().split(' ')[-1]
        if rpmtag in ('name', 'version'):
            pkg_info[rpmtag] = line.strip().split(' ')[-1]

        if rpmtag in ('source', 'source0', 'url') and '://' in line:
            line_value = line.strip().split(' ')[-1]

            for k, v in pkg_info.items():
                line_value = line_value.replace('%{' + k + '}', v)

            # normalize
            gh_url = urllib.parse.urlparse(line_value)
            if gh_url.netloc.endswith('github.io'):
                gh_url = urllib.parse.urlparse(
                    f"https://github.com/{gh_url.netloc.partition('.')[0]}/{gh_url.path.strip('/')}"
                )

            if 'github.com' == gh_url.netloc:
                pkg_info['github_project'] = '/'.join(gh_url.path.split('/')[1:3])

    return pkg_info


def req_addnewrelease(provider, path):
    global NEWRELEASES_API_KEY

    resp = requests.post(
        'https://api.newreleases.io/v1/projects',
        headers={'X-Key': NEWRELEASES_API_KEY},
        json={'provider': provider, 'name': path, 'email_notification': 'instant'},
    )
    LOG.info(
        'adding new release for provider ' f'{provider}/{path}: {resp.status_code}'
    )
    return resp.status_code, resp.json()


def req_newreleases(path):
    global NEWRELEASES_API_KEY

    LOG.debug(f'requesting newreleases: {path}')
    resp = requests.get(
        f'https://api.newreleases.io/v1/{path}', headers={'X-Key': NEWRELEASES_API_KEY}
    )
    resp.raise_for_status()
    return resp.status_code, resp.json()


def changes_to_text(changes):
    """El cheapo cleanup of changes lines"""
    r: str = changes

    if r.endswith(')\n'):
        # Change " Foo bar (#123)" into "Foo bar"
        pr_title = r.rpartition('(#')[0]
        if pr_title:
            r = pr_title.strip() + '\n'
        # Strip (:github-user:`xxx`)
        pr_title: str = r.rpartition('(:github-user:')[0]
        if pr_title:
            r = pr_title.strip() + '\n'

    if r.endswith('`\n'):
        # Strip :github-issue:`1234`
        pr_title = r.rpartition(':github-issue:`')[0]
        if pr_title:
            r = pr_title.strip() + '\n'

    if r.endswith(']\n'):
        # Change " Foo bar [#123]" into "Foo bar"
        pr_title = r.rpartition('[#')[0]
        if pr_title:
            r = pr_title.strip() + '\n'

    if len(r) > 2:
        while r.startswith('\t'):
            r = r[1:]
        if r.startswith('- '):
            r = r.partition(' ')[2]
        if m := re.match(r' *\* (.*)', r):
            r = m.group(1)

    r = '\n'.join(
        textwrap.wrap(r, width=65, initial_indent='  * ', subsequent_indent='    ')
    )

    LOG.debug(f'changes_to_text: converted {changes} to {r}')
    return r.rstrip()


def md_to_text(md):
    """El cheapo markdown to plain text converter"""
    changes = ''
    for line in md.splitlines():
        # Remove GitHub style suffixes
        r = re.sub(r' by \@\S+ in .*$', '', line)
        r = r.strip(' \r\n')
        # Remove links
        r = re.sub(r'\[([^]]+)\]\([^)]+\)', '\\1', r)

        # Remove git commit identifiers
        r = re.sub(r' \([0-9a-f]+\)$', '', r)
        changes += changes_to_text(r + '\n') + '\n'
    return changes


def rst_to_text(rst):
    """El cheapo reStructuredText to plain text converter

    rst (str): The reStructuredText to be converted to plain text.
    returns the converted plain text.
    """
    overrides = {
        'input_encoding': 'unicode',
        'doctitle_xform': True,
        'report_level': 5,
        'initial_header_level': 1,
    }
    parts = publish_parts(
        source=rst,
        source_path=None,
        destination_path=None,
        writer_name='html',
        settings_overrides=overrides,
    )
    html_body: str = str(parts['html_body'])
    html_body = html_body.replace('\n', ' ')
    LOG.debug(f'rst_to_text: converted {rst} to {html_body}')

    changes: str = ''
    bs = BeautifulSoup(html_body, features='lxml')
    for tag in bs.find_all('li'):
        changes_text: str = tag.get_text()
        # Skip subsection titles
        if changes_text in ('Fixed', 'Added'):
            continue
        changes += changes_to_text(changes_text + '\n') + '\n'
    return changes.rstrip() + '\n'


def extract_changes_from_github_release(github_path, oldv, newv):
    """call GitHub  API to fetch new version notices."""
    summary = ''
    path = f'repos/{github_path}/releases'
    LOG.debug(f'requesting github release: {path}')
    resp = requests.get(
        f'https://api.github.com/{path}',
        headers={
            'X-GitHub-Api-Version': '2022-11-28',
            'Accept': 'application/vnd.github+json',
        },
    )

    if resp.status_code > 200:
        LOG.error(f'GitHub Releases returned {resp.status_code}')
        return summary

    resp = resp.json()
    first = True
    start_relversion = pv.parse(newv)
    stop_relversion = pv.parse(oldv)
    for release in resp:
        if release['prerelease'] or release['draft']:
            continue
        release_version = release['tag_name']
        if release_version[0] in ('r', 'v'):
            release_version = release_version[1:]

        LOG.debug(f"checking '{release_version}' for '{oldv}'")
        try:
            relver = pv.parse(release_version)
            if relver > start_relversion:
                LOG.debug(f'skipping over {release_version} > {start_relversion}')
                continue
            if relver.major < stop_relversion.major:
                LOG.debug(f'skipping over {release_version} < {stop_relversion}')
                continue
            if relver.major == stop_relversion.major and relver <= stop_relversion:
                LOG.debug(f'stopping at {release_version} <= {stop_relversion}')
                break
        except pv.InvalidVersion:
            if release_version in (oldv, f'v{oldv}'):
                LOG.debug('f stopping at {release_version}')
                break

        if 'body' in release and release['body']:
            versionnote = release['body']
            if first:
                first = False
            else:
                summary += '- '
            summary += f'update to {release_version}:\n'
            for line in (
                BeautifulSoup(versionnote, features='lxml').get_text().split('\n')
            ):
                summary += md_to_text(line) + '\n'
    return summary


def extract_changes_from_newreleases(github_path, oldv, newv):
    """call newreleases.io API to fetch new version notices."""
    summary = ''

    while True:
        status, resp = req_newreleases(f'projects/github/{github_path}/releases')
        if status == 404:
            status, _ = req_addnewrelease('github', github_path)
            time.sleep(1)
            if status >= 400:
                break
        else:
            break

    if 'code' in resp and resp['code'] > 200:
        print(f'ERROR: GitHub project path {github_path} is incorrect')
        return summary

    for release in resp['releases']:
        if release['version'] in (oldv, f'v{oldv}'):
            break
        if 'has_note' in release:
            _, versionnote = req_newreleases(
                f"projects/github/{github_path}/releases/{release['version']}/note"
            )
            if 'message' in versionnote:
                summary += f"update to {release['version']}:\n"
                for line in (
                    BeautifulSoup(versionnote['message'], features='lxml')
                    .get_text()
                    .split('\n')
                ):
                    summary += changes_to_text(line) + '\n'
    return summary


def extract_update_section(oldv, newv, package_name, infile):
    """Extract from the given text the update section.

    We assume that the relevant part starts with {newv} somewhere isolated and ends
    with {oldv}. e.g. with other words we assume reverse chronological ordering."""
    inupdatesection = False
    update_section: str = ''
    for line in infile:
        line: str = line.decode(encoding='utf-8', errors='ignore')
        if inupdatesection:
            stripped_line: str = line.strip(' \r\n()[]t*#-=:/`')
            if stripped_line:
                # packagename oldversion (releasedate)
                if stripped_line.lower().startswith(package_name.lower()):
                    stripped_line = stripped_line.partition(' ')[2].strip()
                if (
                    stripped_line.startswith(oldv)
                    or stripped_line.startswith(f'({oldv})')
                    or stripped_line.lower().startswith(f'version {oldv}')
                    or stripped_line.endswith(oldv)
                    or stripped_line.endswith(f'{oldv}.0')
                    or ('release' in stripped_line.lower() and oldv in stripped_line)
                ):
                    break
            update_section += line
            continue

        if not inupdatesection and newv in line:
            update_section += line
            inupdatesection = True
    return update_section


def extract_changes_from_tarball(package_information, oldv, newv):
    package_name = package_information['name']
    LOG.debug(f'looking for *{newv}*')
    for fname in glob.iglob(f'*{newv}*'):
        if not (os.path.isfile(fname) and tarfile.is_tarfile(fname)):
            continue

        with tarfile.open(fname) as source:
            LOG.debug(f'Scanning {fname}')
            for candidate in (
                'NEWS',
                'NEWS.adoc',
                'NEWS.md',
                'NEWS.rst',
                'RELEASE.rst',
                'releasenotes.rst',
                'RELEASE_NOTES.rst',
                'versionhistory.rst',
                'HISTORY.rst',
                'HISTORY.md',
                'History.txt',
                'CHANGES.md',
                'CHANGES.rst',
                'CHANGES.txt',
                'CHANGES',
                'CHANGELOG.md',
                'change_log.md',
                'CHANGELOG.rst',
                'Changelog.txt',
                'ChangeLog',
            ):
                for finfo in source.getmembers():
                    if not finfo.isfile():
                        continue
                    name = finfo.name
                    if name.rpartition('/')[2].casefold() == candidate.casefold():
                        LOG.debug(f'found changes file: {candidate}')
                        update_section: str = extract_update_section(
                            oldv, newv, package_name, source.extractfile(name)
                        )
                        changes: str = ''
                        if name.rpartition('.')[2].lower() in ('rst',):
                            changes = rst_to_text(rst=update_section)
                        elif name.rpartition('.')[2].lower() in ('md', 'adoc'):
                            changes = md_to_text(update_section)
                        else:
                            for line in update_section.split('\n'):
                                changes += changes_to_text(line).rstrip() + '\n'

                        changes = changes.rstrip() + '\n'
                        if len(changes) > 4:
                            print(f"update to {newv}:\n{''.join(changes)}")
                            return True
    return False


def main():
    """Main function"""

    with open(os.path.expanduser('~/.config/changesgenrc'), encoding='utf8') as f:
        global NEWRELEASES_API_KEY
        c = configparser.ConfigParser(strict=False)
        c.read_file(f)
        NEWRELEASES_API_KEY = c['DEFAULT'].get('newreleases_api_key', None)

    parse = argparse.ArgumentParser(
        description='Generate OSC vc changes', exit_on_error=False
    )
    parse.add_argument('-d', '--debug', action='store_true')
    parse.add_argument('old', metavar='oldv', type=str, help='Old version', nargs='?')
    parse.add_argument('new', metavar='newv', type=str, help='New version', nargs='?')

    args = parse.parse_args()

    if args.debug:
        LOG.basicConfig(level=LOG.DEBUG)

    package_information = parse_from_spec_file(os.getcwd())

    if 'version' not in package_information:
        LOG.fatal('Cannot determine starting version (not run in osc checkout?)')
        return

    oldv = newv = package_information['version']

    if os.path.exists('.osc'):
        old_package_information = parse_from_spec_file(
            os.path.join(os.getcwd(), '.osc')
        )
        oldv = old_package_information['version']

    if oldv == newv:
        oldv = args.old
        newv = args.new

    if extract_changes_from_tarball(package_information, oldv, newv):
        return

    if not oldv or not newv:
        LOG.fatal(f'Missing oldv {oldv} and newv {newv}')
        return

    summary = None
    if 'github_project' in package_information:
        summary = extract_changes_from_github_release(
            package_information['github_project'], oldv, newv
        )
        if not summary and NEWRELEASES_API_KEY:
            summary = extract_changes_from_newreleases(
                package_information['github_project'], oldv, newv
            )
    if summary and len(summary) > 5:
        print(summary)


main()
