"""Count identifiers that still contain Spanish words, by area.

Progress meter for the naming migration (see glossary.csv). It only looks at
names, never at user-facing text: strings with spaces (screen text, messages,
legal templates) are ignored because they stay in Spanish on purpose.

Areas:
  py:<folder>   function/class/argument/variable/attribute names and module names
  keys:<folder> identifier-like string literals ("anio_auditado", SQL column
                names, dict keys, form field names)
  schema        tables and columns in schema.sql
  routes        URL paths declared in core/
  css           class names in static/css

Usage (from the repository root):
  python3 scripts/naming/count_spanish.py                 # summary table
  python3 scripts/naming/count_spanish.py --details schema
  python3 scripts/naming/count_spanish.py --json > before.json
  python3 scripts/naming/count_spanish.py --compare before.json
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORDS_FILE = Path(__file__).with_name("spanish_words.txt")
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Folders that are counted separately; anything else goes to "other".
AREAS = ("core", "database", "services", "views", "ui", "providers", "scripts", "tests")


def spanish_words() -> set[str]:
    lines = WORDS_FILE.read_text(encoding="utf-8").splitlines()
    return {w.strip() for w in lines if w.strip() and not w.startswith("#")}


def tokens(name: str) -> list[str]:
    """snake_case, kebab-case and CamelCase parts, lowercased."""
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return [t for t in re.split(r"[_\-./]+|(?<=[a-z])(?=\d)", name.lower()) if t]


def is_spanish(name: str, words: set[str]) -> bool:
    return any(t in words for t in tokens(name))


def tracked(pattern: str) -> list[Path]:
    out = subprocess.run(["git", "ls-files", pattern], cwd=ROOT, capture_output=True, text=True, check=True)
    return [ROOT / f for f in out.stdout.split()]


def area_of(path: Path) -> str:
    first = path.relative_to(ROOT).parts[0]
    return first if first in AREAS else "other"


def collect() -> dict[str, set[str]]:
    """{area: set of names} for every area."""
    found: dict[str, set[str]] = defaultdict(set)
    for path in tracked("*.py"):
        if "scripts/naming" in path.as_posix():
            continue  # the tooling mentions Spanish names on purpose
        area = area_of(path)
        found[f"py:{area}"].add(path.stem)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                found[f"py:{area}"].add(node.name)
            elif isinstance(node, ast.arg):
                found[f"py:{area}"].add(node.arg)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                found[f"py:{area}"].add(node.id)
            elif isinstance(node, ast.Attribute):
                found[f"py:{area}"].add(node.attr)
            elif isinstance(node, ast.keyword) and node.arg:
                found[f"py:{area}"].add(node.arg)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                value = node.value
                if IDENTIFIER.match(value):
                    found[f"keys:{area}"].add(value)
                # Form fields, ids and classes written inside HTML strings.
                for attr in re.findall(r'\b(?:name|id|for)="([A-Za-z_][\w-]*)"', value):
                    found[f"keys:{area}"].add(attr)
    schema = (ROOT / "schema.sql").read_text(encoding="utf-8")
    found["schema"].update(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", schema))
    found["schema"].update(re.findall(r"^\s+([a-z_][a-z0-9_]*) (?:TEXT|INTEGER|REAL)", schema, re.M))
    for path in tracked("core/*.py"):
        found["routes"].update(re.findall(r'"(/[a-z][a-z0-9/._-]*)"', path.read_text(encoding="utf-8")))
    for path in tracked("static/css/*.css"):
        found["css"].update(re.findall(r"\.([a-z][a-z0-9-]*)", path.read_text(encoding="utf-8")))
    return found


def summary(found: dict[str, set[str]], words: set[str]) -> dict[str, dict]:
    return {
        area: {"total": len(names), "spanish": sorted(n for n in names if is_spanish(n, words))}
        for area, names in sorted(found.items())
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--details", metavar="AREA", help="list the Spanish names of one area")
    parser.add_argument("--json", action="store_true", help="print the full result as JSON")
    parser.add_argument("--compare", metavar="FILE", help="compare with a previous --json result")
    args = parser.parse_args()

    result = summary(collect(), spanish_words())
    if args.json:
        json.dump(result, sys.stdout, ensure_ascii=False, indent=1)
        return 0
    if args.details:
        for name in result.get(args.details, {}).get("spanish", []):
            print(name)
        return 0
    before = json.loads(Path(args.compare).read_text()) if args.compare else {}
    print(f"{'area':16} {'names':>7} {'spanish':>8} {'%':>6}" + ("   before  change" if before else ""))
    total = spanish = 0
    for area, data in result.items():
        n, s = data["total"], len(data["spanish"])
        total, spanish = total + n, spanish + s
        line = f"{area:16} {n:7} {s:8} {100 * s / max(n, 1):5.1f}%"
        if before:
            prev = len(before.get(area, {}).get("spanish", []))
            line += f"   {prev:6} {s - prev:+7}"
        print(line)
    print(f"{'TOTAL':16} {total:7} {spanish:8} {100 * spanish / max(total, 1):5.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
