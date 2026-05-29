#!/usr/bin/env python3
"""Surgically port new-model entries from model_prices_and_context_window.json
to litellm/model_prices_and_context_window_backup.json.

Use after cherry-picking the focused commits of an upstream "add model X"
PR but skipping its full backup-file regeneration commit (which carries
unrelated drift). Preserves both files' exact whitespace/key order.

Usage:
    ./port_model_entries.py 'opus-4-8'
    ./port_model_entries.py 'opus-4-8' --root model_prices_and_context_window.json --backup litellm/model_prices_and_context_window_backup.json

The substring filter matches on key names (e.g. 'opus-4-8' matches
'claude-opus-4-8', 'us.anthropic.claude-opus-4-8', etc.).

For greptile-style provider_specific_entry list-of-arrays bugs, fix the
root JSON first (this script copies it as-is).
"""
import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional


def extract_block(text: str, key: str) -> Optional[str]:
    """Extract a top-level JSON object's '<key>: { ... },\\n' block from a
    pretty-printed (4-space indent) JSON file as raw text.
    """
    pattern = re.compile(r"^( {4})\"" + re.escape(key) + r"\": \{$", re.MULTILINE)
    m = pattern.search(text)
    if not m:
        return None
    i = m.end()
    depth = 1
    while i < len(text) and depth > 0:
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    end = i
    if text[end : end + 1] == ",":
        end += 1
    if text[end : end + 1] == "\n":
        end += 1
    return text[m.start() : end]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("filter", help="Substring to match against key names.")
    ap.add_argument("--root", default="model_prices_and_context_window.json", type=Path)
    ap.add_argument("--backup", default="litellm/model_prices_and_context_window_backup.json", type=Path)
    args = ap.parse_args()

    root_text = args.root.read_text()
    backup_text = args.backup.read_text()

    root_obj = json.loads(root_text)
    keys = [k for k in root_obj if args.filter in k]
    if not keys:
        print(f"no keys in {args.root} matching {args.filter!r}", file=sys.stderr)
        return 1

    print(f"matched {len(keys)} keys in {args.root}:")
    for k in keys:
        print(f"  {k}")

    to_insert = []
    for k in keys:
        block = extract_block(root_text, k)
        if block is None:
            print(f"  ! could not locate block in root for key {k}", file=sys.stderr)
            return 2
        # If backup already has a top-level entry for this key, refuse — the
        # caller should resolve manually rather than risk drift.
        if re.search(r"^( {4})\"" + re.escape(k) + r"\": \{$", backup_text, re.MULTILINE):
            print(f"  ! key already in backup: {k} (skipping)", file=sys.stderr)
            continue
        to_insert.append(block)

    if not to_insert:
        print("nothing to insert; backup unchanged.")
        return 0

    last_brace = backup_text.rfind("\n}")
    if last_brace == -1:
        print("could not find closing brace of backup JSON", file=sys.stderr)
        return 3
    pre = backup_text[:last_brace]
    if pre.endswith("    }"):
        pre = pre[:-len("    }")] + "    },"
    elif not pre.endswith("    },"):
        # fall back to permissive append
        pass

    insertion = "".join(to_insert)
    if insertion.endswith(",\n"):
        insertion = insertion[:-2] + "\n"
    new_backup = pre + "\n" + insertion + "}\n"

    # Validate the result is parseable JSON before writing.
    try:
        json.loads(new_backup)
    except json.JSONDecodeError as e:
        print(f"resulting JSON is invalid: {e}", file=sys.stderr)
        return 4

    args.backup.write_text(new_backup)
    print(f"\ninserted {len(to_insert)} entries into {args.backup}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
