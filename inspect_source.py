#!/usr/bin/python3
# vim: sw=4 et

import argparse
import datetime
import json
import logging as LOG
import os
import re
from pathlib import Path

import rpm

rpm_ts = rpm.TransactionSet()


CUTOFF_TIMESTAMP = datetime.datetime(2019, 1, 1).timestamp()


def stat_src_changes(fname):
    global rpm_ts

    try:
        fdno = os.open(fname, os.O_RDONLY)

        rpm_ts.setVSFlags(~(rpm.RPMVSF_NEEDPAYLOAD))
        try:
            hdr = rpm_ts.hdrFromFdno(fdno)
        except rpm.error as e:
            LOG.warning(f'{fname}: {e}')
            hdr = None
        if not isinstance(hdr, rpm.hdr):
            hdr = None

    finally:
        rpm_ts.setVSFlags(0)
        os.close(fdno)

    package_name = '%s' % (hdr[rpm.RPMTAG_NAME],)
    package_date = hdr[rpm.RPMTAG_BUILDTIME]

    r = {
        'employee_changes': {},
        'community_changes': {},
        'version_changes': {},
        'patches_changes': {},
        'jira_changes': {},
        'bugs_changes': {},
    }

    for name, time, text in zip(
        hdr[rpm.RPMTAG_CHANGELOGNAME],
        hdr[rpm.RPMTAG_CHANGELOGTIME],
        hdr[rpm.RPMTAG_CHANGELOGTEXT],
    ):
        if time < CUTOFF_TIMESTAMP:
            continue
        changes_timestamp = datetime.datetime.fromtimestamp(time)
        year: str = f'{changes_timestamp.year}Q{(1 + (changes_timestamp.month-1) // 3)}'

        # LOG.debug(f"{fname}: {name}, {time}, {text}")
        if name.partition('@')[2].lower() in ('suse.de', 'suse.cz', 'suse.com'):
            r['employee_changes'][year] = r['employee_changes'].setdefault(year, 0) + 1
        else:
            r['community_changes'][year] = (
                r['community_changes'].setdefault(year, 0) + 1
            )

        patches_mentioned = 0
        for p in re.finditer(r'\.(patch|diff)\b', text, re.IGNORECASE):
            patches_mentioned += 1

        updates_mentioned = 0
        for u in re.finditer(
            r'^[-+*]\s+update.*\d+\.', text, re.IGNORECASE | re.MULTILINE
        ):
            LOG.debug(f'found version update {u.group(0)}')
            updates_mentioned += 1

        jiras_mentioned = 0
        for u in re.finditer(r'jsc#[A-Z]+-\d+', text, re.IGNORECASE | re.MULTILINE):
            jiras_mentioned += 1

        bugs_mentioned = 0
        for b in re.finditer(r'(bsc|bnc|boo)#\d+', text, re.IGNORECASE | re.MULTILINE):
            bugs_mentioned += 1

        if updates_mentioned:
            r['version_changes'][year] = r['version_changes'].setdefault(year, 0) + 1
        elif patches_mentioned:
            r['patches_changes'][year] = r['patches_changes'].setdefault(year, 0) + 1
        if jiras_mentioned:
            r['jira_changes'][year] = r['jira_changes'].setdefault(year, 0) + 1
        if bugs_mentioned:
            r['bugs_changes'][year] = r['bugs_changes'].setdefault(year, 0) + 1

    metric_sum = {}
    for metric in r.keys():
        metric_sum[metric + '_total'] = sum(r[metric].values())
    r.update(metric_sum)

    return package_name, package_date, {package_name: r}


def gather_source_rpms(repodir: Path):
    packages = {}
    r = {}

    for p in repodir.iterdir():
        if (repodir / p).is_dir():
            subdir_pac_set = gather_source_rpms(repodir / p)
            r.update(subdir_pac_set)
            continue

        if not p.name.endswith('.src.rpm') and not p.name.endswith('.nosrc.rpm'):
            continue

        pac_name, package_date, pacstat = stat_src_changes(repodir / p)
        if pac_name not in packages or package_date > packages[pac_name]:
            # print(f'New version of {pac_name} found; {package_date};')
            packages[pac_name] = package_date
            r[pac_name] = pacstat[pac_name]

            # r.update(pacstat)

    return r


def main():
    parse = argparse.ArgumentParser(description='Inspect source DVD')
    parse.add_argument('-d', '--debug', action='store_true')
    parse.add_argument('repodir', nargs='+', help='path to the source DVD repo')
    args = parse.parse_args()

    LOG.basicConfig(
        level=LOG.DEBUG if args.debug else LOG.INFO,
        datefmt='%y-%m-%d %H:%M:%S',
        format='%(asctime)s %(message)s',
    )

    for repostr in args.repodir:
        repo = Path(repostr)
        if not repo.is_dir():
            LOG.fatal(f'Repo {repo} does not exist')
            return

        repo_pacs = gather_source_rpms(repo)

    for tag in (
        'employee_changes',
        'community_changes',
        'version_changes',
        'patches_changes',
        'jira_changes',
        'bugs_changes',
    ):
        print(f'{tag}:')
        print('------')
        report = {}
        for y in (2019, 2020, 2021, 2022, 2023, 2024):
            for q in (1, 2, 3, 4):
                y_sum = 0
                y_quarter = f'{y}Q{q}'
                for pac, stats in repo_pacs.items():
                    if 'kernel' in pac:
                        continue
                    if y_quarter in stats[tag]:
                        y_sum += stats[tag][y_quarter]

                print(f'{y_quarter}: {y_sum}')
                report[y_quarter] = y_sum
        print(report)
        print()
    # print(json.dumps(repo_pacs))


if __name__ == '__main__':
    main()
