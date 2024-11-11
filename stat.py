#!/usr/bin/env python3

import datetime as dt
from io import StringIO
import osc.conf
import json
from dateutil.relativedelta import relativedelta
import requests
import xml.etree.ElementTree as ET
from osc.core import http_GET, makeurl

def _month(yyyy_mm: str) -> tuple[str, str]:
    """Helper to return start_date and end_date of a month as yyyy-mm-dd"""
    year, month = map(int, yyyy_mm.split("-"))
    first = dt.date(year, month, 1)
    last = first + relativedelta(months=1) - relativedelta(days=1)
    return str(first), str(last)


def _last_month() -> tuple[str, str]:
    """Helper to return start_date and end_date of the previous month as yyyy-mm-dd"""
    today = dt.date.today()
    d = today - relativedelta(months=1)
    return _month(d.isoformat()[:7])


def _this_month() -> str:
    """Helper to return start_date of the current month as yyyy-mm-dd.
    No end_date needed."""
    today = dt.date.today()
    return _month(today.isoformat()[:7])[0]


def download_monthly(package) -> int:
    import pypistats

    results = json.loads(pypistats.overall(package, mirrors=True,
            start_date=_this_month(),
                format="json", total="monthly", color="no", verbose=False))
    return results["data"][0]["downloads"]


def get_pkg_list(project) -> list[str]:
    pkglistxml = http_GET(
        makeurl(
            "https://api.opensuse.org/",
            ['source', project],
            query={'view': 'info', 'parse': 1},
        )
    )

    ET_root = ET.fromstring(pkglistxml.read())
    pkgs = {}
    for elem in ET_root.iterfind("sourceinfo"):
        pkg = elem.attrib["package"]
        if ':' in pkg or not pkg.startswith("python-"):
            continue

        if (l := elem.find("linked")) is None:
            # not submitted to Factory, ignore
            continue

        if (v := elem.find("version")) is not None:
            pkg_version = v.text
            pkgs[pkg] = pkg_version
    return pkgs

def lookup_pypi(pypi_name, pkg_version):
    r = requests.get(f"https://pypi.io/pypi/{pypi_name}/json")
    if r.status_code >= 400:
        return None, None
    r = json.loads(r.text)

    classifiers = r["info"].get("classifiers", [])

    if "Programming Language :: Python :: 3.12" not in classifiers:
        pass # print(f"WARN: {pypi_name} not compatible with 3.12!")

    last_version = r["info"]["version"]
    last_version_date = None
    version_is_yanked = True

    for release in r["releases"][last_version]:
        if release["packagetype"] == "sdist":
            last_version_date = dt.datetime.fromisoformat(release["upload_time"])
            version_is_yanked = release["yanked"]

    if not last_version_date:
        return None, None

    if version_is_yanked:
        print(f"ERROR: version package {pypi_name} {last_version} is yanked!!")

    pkg_version_date = None
    if pkg_version in r["releases"]:
        for release in r["releases"][pkg_version]:
            if release["packagetype"] == "sdist":
                pkg_version_date = dt.datetime.fromisoformat(release["upload_time"])
                if release["yanked"]:
                    print(f"ERROR: packaged version for {pypi_name} - {pkg_version} is marked as yanked!")

    if last_version_date and dt.datetime.now() - last_version_date < dt.timedelta(weeks=4):
        return None, None

    if pkg_version == last_version:
        return None, None

    return pkg_version_date, last_version


def main() -> None:

    osc.conf.get_config()

    pkgs = get_pkg_list("devel:languages:python")
    for pkg in pkgs:
        if pkg.startswith("python-"):
            pypi_name = pkg.partition("-")[2]
            pkg_version = pkgs[pkg]

            r, latest_version = lookup_pypi(pypi_name, pkg_version)
            if not r:
                continue
            age = dt.datetime.now() - r
            if age > dt.timedelta(days=5 * 365):
                print(f"{pypi_name} (latest is {latest_version}): packaged ver {pkg_version} is {age.days} days old")


if __name__ == "__main__":
    main()
