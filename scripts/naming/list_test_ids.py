"""List the test ids of the suite without running it, and compare two lists.

Check A of every naming phase: no test may disappear. Ids are written as
"Class.method" by default (module left out) so that moving a test class to
another file is not reported as a loss. Renamed tests are declared in a CSV
map ("old,new" per line, same "Class.method" format).

Usage (from the repository root):
  python3 scripts/naming/list_test_ids.py > before.txt
  python3 scripts/naming/list_test_ids.py --with-module > before_modules.txt
  python3 scripts/naming/list_test_ids.py --compare before.txt [--renames renames.csv]
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_ids(with_module: bool = False) -> list[str]:
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    suite = unittest.defaultTestLoader.discover("tests")

    def walk(item):
        for test in item:
            if isinstance(test, unittest.TestSuite):
                yield from walk(test)
            else:
                yield test.id()

    ids = sorted(walk(suite))
    broken = [i for i in ids if i.startswith("unittest.loader._FailedTest")]
    if broken:
        raise SystemExit("Test modules that do not import: " + ", ".join(i.rsplit(".", 1)[-1] for i in broken))
    return ids if with_module else sorted(i.split(".", 1)[1] for i in ids)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--with-module", action="store_true", help="keep the module in each id")
    parser.add_argument("--compare", metavar="FILE", help="previous list to compare with")
    parser.add_argument("--renames", metavar="CSV", help="old,new pairs of renamed tests")
    args = parser.parse_args()

    ids = test_ids(args.with_module)
    if not args.compare:
        print("\n".join(ids))
        return 0

    before = [line.strip() for line in Path(args.compare).read_text().splitlines() if line.strip()]
    renames: dict[str, str] = {}
    if args.renames:
        with open(args.renames, newline="", encoding="utf-8") as stream:
            renames = {old.strip(): new.strip() for old, new in csv.reader(stream) if old.strip()}
    expected = sorted(renames.get(i, i) for i in before)
    missing = sorted(set(expected) - set(ids))
    added = sorted(set(ids) - set(expected))
    print(f"before: {len(before)}  now: {len(ids)}  renamed: {len(renames)}")
    for i in missing:
        print(f"  MISSING  {i}")
    for i in added:
        print(f"  NEW      {i}")
    if missing or len(expected) != len(ids):
        print("FAIL: tests were lost or duplicated")
        return 1
    print("OK: same tests" + (" (new tests listed above)" if added else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
