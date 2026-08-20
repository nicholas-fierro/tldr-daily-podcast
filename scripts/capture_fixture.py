#!/usr/bin/env python3
"""Capture a real edition's HTML as a test fixture.

The committed fixture is synthetic — written to the structure documented in
docs/HANDOFF.md, not captured from tldr.tech (the build environment could not
reach it). Run this once from a machine with normal network access to replace
it with the real thing, which is what the handoff actually asks for:

    python scripts/capture_fixture.py              # today's edition
    python scripts/capture_fixture.py 2026-08-20   # a specific one

Then point tests/test_parse.py's REAL_FIXTURE at the file it writes and check
that the assertions still hold. If they don't, the parser is wrong about the
real page and that is exactly what this fixture exists to tell you.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import fetch, parse  # noqa: E402

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def main() -> int:
    date = sys.argv[1] if len(sys.argv) > 1 else None
    edition = fetch.fetch_edition(date=date)

    destination = FIXTURES / f"tldr-{edition.date}.html"
    destination.write_text(edition.html, encoding="utf-8")

    items = parse.parse_edition(edition.html)
    print(f"wrote {destination} ({len(edition.html):,} bytes)")
    print(f"parsed {len(items)} items:\n")
    for item in items:
        read_time = f"{item.read_time}m" if item.read_time else "-"
        print(f"  [{item.section}] {item.title} ({read_time})\n    {item.url}")
    print("\nCheck this list for ads before trusting it as a fixture.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
