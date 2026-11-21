"""Check the operating-system permissions the engine needs, and ask for the missing ones.

    uv run python -m tools.permissions             # what is granted
    uv run python -m tools.permissions --request   # show the system prompts for the rest

On macOS the permissions belong to the app that runs the engine (the terminal while
developing), and that app has to be restarted after granting one. Windows needs none.
"""

from __future__ import annotations

import argparse

from engine import native


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tools.permissions", description=__doc__)
    parser.add_argument("--request", action="store_true", help="ask for missing permissions")
    args = parser.parse_args(argv)

    backend = native.current()
    items = backend.permissions(request=args.request)
    if not items:
        print(f"{backend.name}: no permissions to grant")
        return 0

    for item in items:
        mark = "ok     " if item.granted else "MISSING"
        print(f"  {mark} {item.name:<17} {item.needed_for}")
        if not item.granted:
            print(f"          {item.how}")

    if any(not item.granted for item in items):
        return 1
    print("\nall granted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
