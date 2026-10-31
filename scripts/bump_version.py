#!/usr/bin/env python3
"""Auto version bump. Main-e kono change merge holei GitHub Action eta chalay.

Niyom (commit / PR message ba branch-er nam theke):
  major  -> "BREAKING" likha thakle, ba "feat!:" / "fix!:" , ba branch "major/..."       1.5.0 -> 2.0.0
  minor  -> "feat" ("feat:" ba branch "feat/...")                                      1.5.0 -> 1.6.0
  patch  -> baki shob (fix, perf, chore, docs, ba kono prefix chhara)                  1.5.0 -> 1.5.1

Usage: bump_version.py "<commit message>"   (VERSION ar CHANGELOG.md bodlay, notun version print kore)
"""
import datetime, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def decide(msg):
    low = msg.lower()
    if "breaking" in low or re.search(r"\b\w+(\([^)]*\))?!:", low) or re.search(r"(^|[\s/])major/", low):
        return "major"
    if re.search(r"(^|[\s/(])feat(ure)?(/|:|\()", low):
        return "minor"
    return "patch"


def bump(version, kind):
    a, b, c = (int(x) for x in version.split("."))
    if kind == "major":
        return f"{a + 1}.0.0"
    if kind == "minor":
        return f"{a}.{b + 1}.0"
    return f"{a}.{b}.{c + 1}"


def subject(msg):
    lines = [l.strip() for l in msg.splitlines() if l.strip()]
    # merge commit hole 2nd line-i asol PR title
    if lines and lines[0].lower().startswith("merge pull request") and len(lines) > 1:
        return lines[1]
    return lines[0] if lines else "update"


def main():
    msg = sys.argv[1] if len(sys.argv) > 1 else ""
    vf = ROOT / "VERSION"
    cur = vf.read_text().strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", cur):
        sys.exit(f"bad VERSION: {cur!r}")
    kind = decide(msg)
    new = bump(cur, kind)
    vf.write_text(new + "\n")
    cl = ROOT / "CHANGELOG.md"
    old = cl.read_text() if cl.exists() else "# Changelog\n"
    head, _, rest = old.partition("\n")
    entry = f"\n## v{new} - {datetime.date.today().isoformat()} ({kind})\n- {subject(msg)}\n"
    cl.write_text(head + "\n" + entry + rest)
    print(new)


if __name__ == "__main__":
    main()
