"""macOS: the privacy permissions the engine needs, checked and requested.

macOS grants these to the *app* that runs the engine, not to Python: the terminal while
developing (Terminal, iTerm, the IDE or the Claude app whose terminal it is), the packaged
app later. A newly granted permission only takes effect after that app restarts.
"""

from __future__ import annotations

import os
import subprocess

import ApplicationServices as AX
import Quartz

from engine.native import Permission

_PANE = "System Settings → Privacy & Security → "


def responsible_app() -> str:
    """The ``.app`` that owns this process's permissions, as best it can be told.

    macOS holds the outermost app responsible (Terminal, an IDE, the Claude app), so this
    walks up every parent process and keeps the last app bundle it passes. Empty when there
    is none (a login shell over SSH, say).
    """
    found = ""
    pid = os.getppid()
    for _ in range(64):
        if pid <= 1:
            break
        try:
            out = subprocess.run(
                ["ps", "-o", "ppid=,comm=", "-p", str(pid)],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            break
        if not out:
            break
        parent, _, command = out.partition(" ")
        marker = command.find(".app/")
        if marker != -1:
            found = command[: marker + len(".app")]
        pid = int(parent)
    return found


def function_keys_are_standard() -> bool | None:
    """Whether F1-F12 send key codes without fn (``com.apple.keyboard.fnState``)."""
    try:
        out = subprocess.run(
            ["defaults", "read", "-g", "com.apple.keyboard.fnState"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return False  # never set: the default is media keys
    return out.stdout.strip() == "1"


def permissions(*, request: bool = False) -> list[Permission]:
    if request:
        if not Quartz.CGPreflightScreenCaptureAccess():
            Quartz.CGRequestScreenCaptureAccess()
        if not AX.AXIsProcessTrusted():
            AX.AXIsProcessTrustedWithOptions({AX.kAXTrustedCheckOptionPrompt: True})
        if not Quartz.CGPreflightListenEventAccess():
            Quartz.CGRequestListenEventAccess()

    app = responsible_app()
    owner = (
        f" for {os.path.basename(app)}, then restart it" if app else ", then restart the terminal"
    )
    return [
        Permission(
            name="Screen Recording",
            granted=bool(Quartz.CGPreflightScreenCaptureAccess()),
            needed_for="hearing the game (ScreenCaptureKit audio), window titles, capture",
            how=_PANE + "Screen & System Audio Recording" + owner,
        ),
        Permission(
            name="Accessibility",
            granted=bool(AX.AXIsProcessTrusted()),
            needed_for="pressing keys and clicking in the game, focusing it, hotkeys",
            how=_PANE + "Accessibility" + owner,
        ),
        Permission(
            name="Input Monitoring",
            granted=bool(Quartz.CGPreflightListenEventAccess()),
            needed_for="global hotkeys (F9 / F10 / F12) while WoW is in front",
            how=_PANE + "Input Monitoring" + owner,
        ),
        Permission(
            name="Function keys",
            granted=function_keys_are_standard() is not False,
            needed_for="F9 / F10 / F12 without holding fn (otherwise they change volume)",
            how=(
                "System Settings → Keyboard → Keyboard Shortcuts → Function Keys → "
                '"Use F1, F2, etc. keys as standard function keys"'
            ),
        ),
    ]
