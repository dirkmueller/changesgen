#!/usr/bin/python3

import argparse
from bs4 import BeautifulSoup
import glob
import time
import logging as LOG
import requests
import textwrap
import urllib.parse

newreleases_api_key = 'mfjta88m07fphmda72ef6ngsz8ab70epqtk0'


def parse_from_spec_file():
    primary_spec = sorted(glob.glob('*.spec'), key=len)

    pkg_info = {}

    if not len(primary_spec):
        return pkg_info

    for line in open(primary_spec[0]):

        line = line.lower()

        if line.partition(' ')[0] in ('%description', '%package'):
            break

        line_keyword = line.partition(':')[0]

        if line_keyword in ('name', 'version'):
            pkg_info[line_keyword] = line.strip().split(' ')[-1]

        if ((line_keyword in ('source', 'source0', 'url')) and
                'github.com' in line):
            gh_url = line.strip().split(' ')[-1]

            for k in pkg_info:
                gh_url = gh_url.replace('%{' + k + '}', pkg_info[k])

            # normalize
            o = urllib.parse.urlparse(gh_url)
            pkg_info['github_project'] = '/'.join(o.path.split('/')[1:3])

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


def extract_changes_from_github_releases(github_path, oldv, newv):

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

    for release in resp['releases']:
        if release['version'] in (oldv, f"v{oldv}"):
            break
        if 'has_note' in release:
            summary += f"- update to {release['version']}:\n"
            _, versionnote = req_newreleases(f"projects/github/{github_path}/releases/{release['version']}/note")
            for line in BeautifulSoup(versionnote['message'], features="lxml").get_text().split('\n'):
                if len(line) > 2 and not line.startswith(' '):
                    line = '  * ' + "\n    ".join(textwrap.wrap(line, width=72))
                if line.endswith(')\n'):
                    line = line.rpartition('(')[0].strip() + '\n'
                line = line.rstrip()
                summary += line + '\n'

    return summary


def main():

    LOG.basicConfig(level=LOG.DEBUG)

    parse = argparse.ArgumentParser(description='Generate OSC vc changes')
    parse.add_argument(
        'old', metavar='oldv', type=str, help='Old version')
    parse.add_argument(
        'new', metavar='newv', type=str, help='New version')

    args = parse.parse_args()

    package_information = parse_from_spec_file()

    if 'github_project' in package_information:
        print(extract_changes_from_github_releases(
            package_information['github_project'], args.old, args.new))


main()
