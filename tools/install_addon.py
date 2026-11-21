"""Install the FishBridge addon into the game's AddOns folder.

    uv run python -m tools.install_addon            # find the folder from the running game
    uv run python -m tools.install_addon --check    # say where it would go, copy nothing
    uv run python -m tools.install_addon --dir "D:/Games/WoW/_retail_/Interface/AddOns"

With the game running, the AddOns folder is found next to its executable
(``<game folder>/Interface/AddOns``; on macOS the game folder is the one holding
``World of Warcraft.app``, e.g. ``/Applications/World of Warcraft/_forever_``). Without
it, pass ``--dir``. An older copy of FishBridge there is replaced; nothing else in the
folder is touched. Reload the UI in game
(``/reload``) or restart it, and enable FishBridge in the AddOns list if it is off.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from engine import capture

ADDON = Path(__file__).resolve().parents[1] / "addon" / "FishBridge"


def game_folder(executable: Path) -> Path:
    """The folder holding ``Interface/``: next to ``Wow.exe`` on Windows, next to
    ``World of Warcraft.app`` on macOS (the executable sits inside the bundle)."""
    for parent in executable.parents:
        if parent.suffix == ".app":
            return parent.parent
    return executable.parent


def addons_dir_from_game(process_name: str | None) -> Path:
    window = capture.find_game_window(process_name)
    exe = capture.process_path(window.pid)
    if not exe:
        msg = "could not read where the game is installed; pass --dir"
        raise capture.CaptureError(msg)
    return game_folder(Path(exe)) / "Interface" / "AddOns"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tools.install_addon", description=__doc__)
    parser.add_argument("--dir", type=Path, default=None, help="the game's Interface/AddOns folder")
    parser.add_argument("--process", default=None, help="game executable, e.g. Wow.exe")
    parser.add_argument("--check", action="store_true", help="show the target, copy nothing")
    args = parser.parse_args(argv)

    try:
        target_root = args.dir or addons_dir_from_game(args.process)
    except capture.CaptureError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("start the game first, or pass --dir", file=sys.stderr)
        return 2

    if not target_root.is_dir():
        print(f"error: {target_root} does not exist", file=sys.stderr)
        if args.dir is None:
            print(
                "the game was found, but it has no Interface/AddOns folder; pass --dir",
                file=sys.stderr,
            )
        return 2

    target = target_root / ADDON.name
    print(f"addon  : {ADDON}")
    print(f"target : {target}{' (replacing the copy there)' if target.exists() else ''}")
    if args.check:
        return 0

    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(ADDON, target)
    print("done   : /reload in game, and enable FishBridge in the AddOns list if it is off")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
