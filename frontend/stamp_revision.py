#!/usr/bin/env python3
"""Stamp today's date into the footer's revision <time> element.

The UTHealth Houston web standards require a creation or revision date in the
footer. Run this immediately before copying the front-end to the mirror repo, so
the date reflects the deploy:

    ./frontend/stamp_revision.py frontend/index.html

Idempotent — running it twice in a day is a no-op. Exits non-zero if it cannot
find exactly one revision element, so a silent markup change never leaves a
stale date sitting in production.
"""
import datetime
import pathlib
import re
import sys

PATTERN = re.compile(r'<time id="revdate"[^>]*>[^<]*</time>')


def main() -> int:
    path = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "index.html")
    if not path.is_file():
        print(f"ERROR: {path} not found", file=sys.stderr)
        return 1

    today = datetime.date.today()
    html = path.read_text(encoding="utf-8")

    found = len(PATTERN.findall(html))
    if found != 1:
        print(f"ERROR: expected exactly one <time id=\"revdate\"> in {path}, found {found}",
              file=sys.stderr)
        return 1

    stamped = f'<time id="revdate" datetime="{today.isoformat()}">' \
              f'{today.strftime("%B")} {today.day}, {today.year}</time>'
    updated = PATTERN.sub(stamped, html)

    if updated == html:
        print(f"revision date already {today.isoformat()} — nothing to do")
        return 0

    path.write_text(updated, encoding="utf-8")
    print(f"stamped revision date {today.isoformat()} into {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
