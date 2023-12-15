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
from bs4 import BeautifulSoup
import glob
import time
import logging as LOG
import os
import re
import requests
import tarfile
import textwrap
import configparser
import packaging.version as pv
import urllib.parse

from subprocess import Popen, PIPE, STDOUT

newreleases_api_key = None


def parse_from_spec_file(path):
    primary_spec = sorted(glob.glob(os.path.join(path, '*.spec')), key=len)

    pkg_info = {}

    if not len(primary_spec):
        return pkg_info

    parsed_spec = Popen(('rpmspec', '-P', primary_spec[0]), stdout=PIPE, stderr=STDOUT)
    while True:
        line = parsed_spec.stdout.readline().decode('utf-8')
        if not line:
            break

        if line.partition(' ')[0] in ('%description', '%package'):
            break

        line_keyword = line.partition(':')[0].lower()

        if line_keyword in ('source', 'source0'):
            pkg_info['source'] = line.strip().split(' ')[-1]

        if line_keyword in ('name', 'version'):
            pkg_info[line_keyword] = line.strip().split(' ')[-1]

        if (line_keyword in ('source', 'source0', 'url') and '://' in line):
            line_value = line.strip().split(' ')[-1]

            for k in pkg_info:
                line_value = line_value.replace('%{' + k + '}', pkg_info[k])

            # normalize
            gh_url = urllib.parse.urlparse(line_value)
            if gh_url.netloc.endswith('github.io'):
                gh_url = urllib.parse.urlparse(f"https://github.com/{gh_url.netloc.partition('.')[0]}/{gh_url.path.strip('/')}")

            if 'github.com' == gh_url.netloc:
                pkg_info['github_project'] = '/'.join(gh_url.path.split('/')[1:3])

    return pkg_info


def req_addnewrelease(provider, path):
    global newreleases_api_key

    resp = requests.post(
        "https://api.newreleases.io/v1/projects",
        headers={'X-Key': newreleases_api_key},
        json={
            'provider': provider,
            'name': path,
            'email_notification': 'instant'
        }
    )
    LOG.info("adding new release for provider "
             f"{provider}/{path}: {resp.status_code}")
    return resp.status_code, resp.json()


def req_newreleases(path):
    global newreleases_api_key

    LOG.debug(f"requesting newreleases: {path}")
    resp = requests.get(
        f'https://api.newreleases.io/v1/{path}',
        headers={'X-Key': newreleases_api_key}
    )

    if resp.status_code == 429:
        LOG.error("Hit api request limit on newrelease.io")
        raise

    return resp.status_code, resp.json()


def changes_to_text(changes):
    """El cheapo cleanup of changes lines"""
    r = changes

    if r.endswith(')\n'):
        # Change " Foo bar (#123)" into "Foo bar"
        pr_title = r.rpartition('(#')[0]
        if pr_title:
            r = pr_title.strip() + '\n'

    if len(r) > 2:
        while r.startswith('\t'):
            r = r[1:]
        if r.startswith('- '):
            r = r.partition(' ')[2]
        if m := re.match(r' *\* (.*)', r):
            r = m.group(1)

    r = "\n".join(
        textwrap.wrap(
            r, width=65, initial_indent="  * ", subsequent_indent="    "))

    LOG.debug(f"converted {changes} to {r}")
    return r.rstrip()


def md_to_text(md):
    """El cheapo markdown to plain text converter"""
    # Remove GitHub style suffixes
    r = re.sub(r' by \@\S+ in .*$', '', md)
    r = r.strip(" \r\n")
    # Remove links
    r = re.sub(r'\[([^]]+)\]\([^)]+\)', '\\1', r)

    # Remove git commit identifiers
    r = re.sub(r' \([0-9a-f]+\)$', '', r)
    return changes_to_text(r)


def extract_changes_from_github_release(github_path, oldv, newv):
    """call GitHub  API to fetch new version notices."""
    summary = ''
    path = f"repos/{github_path}/releases"
    LOG.debug(f"requesting github release: {path}")
    resp = requests.get(
        f'https://api.github.com/{path}',
        headers={
            'X-GitHub-Api-Version': '2022-11-28',
            'Accept': 'application/vnd.github+json'})

    if resp.status_code > 200:
        LOG.error(f"GitHub Releases returned {resp.status_code}")
        return summary

    resp = resp.json()
    first = True
    for release in resp:
        if release['prerelease'] or release['draft']:
            continue
        release_version = release['tag_name']
        if release_version[0] in ('r', 'v'):
            release_version = release_version[1:]

        LOG.debug(f"checking '{release_version}' for '{oldv}'")
        try:
            if pv.parse(release_version) <= pv.parse(oldv):
                LOG.debug(f"found {release_version} <= {oldv}")
                break
        except pv.InvalidVersion:
            if release_version in (oldv, f"v{oldv}"):
                LOG.debug("f stopping at {release_version}")
                break

        if 'body' in release:
            versionnote = release['body']
            if first:
                first = False
            else:
                summary += '- '
            summary += f"update to {release_version}:\n"
            for line in BeautifulSoup(versionnote, features="lxml").get_text().split('\n'):

                summary += md_to_text(line) + '\n'
    return summary


def extract_changes_from_newreleases(github_path, oldv, newv):
    """call newreleases.io API to fetch new version notices."""
    summary = ''

    while True:
        status, resp = req_newreleases(f"projects/github/{github_path}/releases")
        if status == 404:
            status, _ = req_addnewrelease("github", github_path)
            time.sleep(1)
            if status >= 400:
                break
        else:
            break

    if 'code' in resp and resp['code'] > 200:
        print(f'ERROR: GitHub project path {github_path} is incorrect')
        return summary

    for release in resp['releases']:
        if release['version'] in (oldv, f"v{oldv}"):
            break
        if 'has_note' in release:
            _, versionnote = req_newreleases(
                f"projects/github/{github_path}/releases/{release['version']}/note")
            if 'message' in versionnote:
                summary += f"update to {release['version']}:\n"
                for line in BeautifulSoup(versionnote['message'], features="lxml").get_text().split('\n'):
                    summary += changes_to_text(line) + '\n'
    return summary


def extract_changes_from_tarball(package_information, oldv, newv):
    package_name = package_information['name']
    LOG.debug(f"looking for *{newv}*")
    for fname in glob.iglob(f"*{newv}*"):
        if not (os.path.isfile(fname) and tarfile.is_tarfile(fname)):
            continue

        with tarfile.open(fname) as source:
            LOG.debug(f"Scanning {fname}")
            for candidate in (
                    'NEWS', 'NEWS.adoc', 'NEWS.md', 'NEWS.rst',
                    'RELEASE.rst', 'releasenotes.rst', 'versionhistory.rst',
                    'HISTORY.rst', 'HISTORY.md', 'History.txt',
                    'CHANGES.md', 'CHANGES.rst', 'CHANGES.txt', 'CHANGES',
                    'CHANGELOG.md', 'change_log.md', 'CHANGELOG.rst', 'Changelog.txt',
                    'ChangeLog', 'changelog'):
                for finfo in source.getmembers():
                    if not finfo.isfile():
                        continue
                    name = finfo.name
                    if name.rpartition('/')[2].casefold() == candidate.casefold():
                        LOG.debug(f'found changes file: {candidate}')
                        inupdatesection = False
                        changes = []
                        for line in source.extractfile(name):
                            line = line.decode(encoding="utf-8",
                                               errors='ignore')
                            if inupdatesection:
                                stripped_line = line.strip(" \r\n()[]t*#-=:/")
                                if not stripped_line:
                                    continue
                                LOG.debug(f"stripped_line {stripped_line}")
                                # packagename oldversion (releasedate)
                                if stripped_line.lower().startswith(package_name.lower()):
                                    stripped_line = stripped_line.partition(' ')[2].strip()
                                if (stripped_line.startswith(oldv) or
                                        stripped_line.startswith(f"({oldv})") or
                                        stripped_line.lower().startswith(f"version {oldv}") or
                                        stripped_line.endswith(oldv) or
                                        stripped_line.endswith(f"{oldv}.0") or
                                        ('release' in stripped_line.lower() and oldv in stripped_line)):
                                    break
                                if name.rpartition('.')[2].lower() in ('md', 'adoc', 'rst'):
                                    line = md_to_text(line)
                                else:
                                    line = changes_to_text(line)

                                changes.append(line.rstrip() + '\n')
                                continue

                            if not inupdatesection and newv in line:
                                inupdatesection = True
                        if len(changes):
                            print(f"update to {newv}:\n{''.join(changes)}")
                            return True
                            break
                        pass
                pass
    return False


def main():
    with open(os.path.expanduser("~/.config/changesgenrc")) as f:
        global newreleases_api_key
        c = configparser.ConfigParser(strict=False)
        c.read_file(f)
        newreleases_api_key = c['DEFAULT'].get('newreleases_api_key', None)

    parse = argparse.ArgumentParser(
        description='Generate OSC vc changes', exit_on_error=False)
    parse.add_argument('-d', '--debug', action='store_true')
    parse.add_argument(
        'old', metavar='oldv', type=str, help='Old version', nargs='?')
    parse.add_argument(
        'new', metavar='newv', type=str, help='New version', nargs='?')

    args = parse.parse_args()

    if args.debug:
        LOG.basicConfig(level=LOG.DEBUG)

    package_information = parse_from_spec_file(os.getcwd())

    if 'version' not in package_information:
        LOG.fatal("Cannot determine starting version (not run in osc checkout?)")
        return

    oldv = newv = package_information['version']

    if os.path.exists('.osc'):
        old_package_information = parse_from_spec_file(os.path.join(os.getcwd(), '.osc'))
        oldv = old_package_information['version']

    if oldv == newv:
        oldv = args.old
        newv = args.new

    if extract_changes_from_tarball(package_information, oldv, newv):
        return

    if not oldv or not newv:
        LOG.fatal(f"Missing oldv {oldv} and newv {newv}")
        return

    summary = None
    if 'github_project' in package_information:
        summary = extract_changes_from_github_release(
            package_information['github_project'], oldv, newv)
        if not summary and newreleases_api_key:
            summary = extract_changes_from_newreleases(
                package_information['github_project'], oldv, newv)
    if summary and len(summary) > 5:
        print(summary)


main()
