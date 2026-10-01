#!/usr/bin/env python3
"""Apply a one-off data fix (data-fixes/*.json) to freshly downloaded data files.

Run by the cron agent against the CURRENT main versions of data/launches.json,
data/archive.json and data/ideas.json, then PUT the changed files back. Only
the fields named in the fix file are touched, so it's safe to apply on top of
newer data. Re-running is a no-op.

Usage:
  python3 scripts/apply_data_fix.py data-fixes/2026-10-01-public-use-cases.json [--data-dir data] [--dry-run]
"""

import argparse
import json
import os
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("fix")
    ap.add_argument("--data-dir", default=os.path.join(os.path.dirname(__file__), "..", "data"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    fix = json.load(open(args.fix))
    changed_files = []

    for name in ("launches.json", "archive.json"):
        path = os.path.join(args.data_dir, name)
        if not os.path.exists(path):
            continue
        data = json.load(open(path))
        n = 0
        for entry in data.get("launches", []):
            new = fix.get("launches", {}).get(entry.get("id"))
            if new and entry.get("implementation_idea") != new:
                entry["implementation_idea"] = new
                n += 1
        print("%s: %d implementation_idea rewritten" % (name, n))
        if n:
            changed_files.append((path, data))

    path = os.path.join(args.data_dir, "ideas.json")
    if os.path.exists(path):
        data = json.load(open(path))
        n = 0
        for idea in data.get("ideas", []):
            for b in idea.get("build_with", []) or []:
                new = fix.get("ideas_build_with", {}).get(b.get("entry_id"))
                if new:
                    b["entry_id"] = new
                    n += 1
        print("ideas.json: %d build_with ids repaired" % n)
        if n:
            changed_files.append((path, data))

    if args.dry_run:
        print("dry run: nothing written")
        return 0
    for path, data in changed_files:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        print("wrote", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
