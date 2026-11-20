"""The desktop app: engine service and UI in one window on the second monitor.

    uv run python -m engine.app               # needs the UI built: npm --prefix ui run build
    uv run python -m engine.app --dev         # load the Vite dev server instead
    uv run python -m engine.app --simulate    # no game: Start runs a simulated session

The server runs on a background thread; ``pywebview`` owns the main thread, as GUI loops
on Windows must. The window opens on the monitor chosen in Settings (the second one by
default), where it was last left if that place is still on that monitor, and remembers its
position and size when it closes.

**Focus.** The window takes focus once, when you open it. After that nothing in the UI
ever asks for focus, and pressing Start hands focus to WoW, so a running bot is never
interrupted by its own window. Clicking into the window while fishing does take focus away
from WoW — and the bot pauses, as it should whenever keys could land in the wrong place.

Closing the window stops the bot and the server. To keep fishing with no window at all,
run ``engine.server`` on its own instead.
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from dataclasses import dataclass

import uvicorn
import webview

from engine import capture
from engine import config as cfg
from engine.server import UI_DIST, build_controller, create_app

DEV_URL = "http://localhost:5173"
TITLE = "Fishbot"
MIN_SIZE = (960, 640)
BACKGROUND = "#0E1A21"  # the UI's abyss, so the window never flashes white while loading
STARTUP_TIMEOUT_S = 10.0

logger = logging.getLogger("app")


@dataclass(frozen=True, slots=True)
class Placement:
    x: int
    y: int
    width: int
    height: int


def place_window(state: cfg.WindowState, monitors: list[capture.MonitorInfo]) -> Placement:
    """Where to open the window: the remembered spot if it is on the chosen monitor,
    otherwise centred on that monitor's work area (or the primary, if it is gone)."""
    if not monitors:
        return Placement(100, 100, state.width, state.height)
    target = next((m for m in monitors if m.index == state.monitor_index), None)
    if target is None:
        target = next((m for m in monitors if m.is_primary), monitors[0])
    area = target.work_rect

    width = min(max(state.width, MIN_SIZE[0]), area.width)
    height = min(max(state.height, MIN_SIZE[1]), area.height)

    if state.x is not None and state.y is not None:
        centre_x = state.x + width // 2
        centre_y = state.y + height // 2
        if area.contains(centre_x, centre_y):
            x = min(max(state.x, area.left), area.right - width)
            y = min(max(state.y, area.top), area.bottom - height)
            return Placement(x, y, width, height)

    return Placement(
        area.left + (area.width - width) // 2,
        area.top + (area.height - height) // 2,
        width,
        height,
    )


def monitor_index_at(x: int, y: int, monitors: list[capture.MonitorInfo]) -> int | None:
    for monitor in monitors:
        if monitor.rect.contains(x, y):
            return monitor.index
    return None


def remember(window: webview.Window, controller_settings: cfg.Settings) -> cfg.Settings:
    """The settings with the window's current monitor, position and size."""
    monitors = capture.list_monitors()
    x, y, width, height = int(window.x), int(window.y), int(window.width), int(window.height)
    index = monitor_index_at(x + width // 2, y + height // 2, monitors)
    updated = controller_settings.window.model_copy(
        update={
            "x": x,
            "y": y,
            "width": width,
            "height": height,
            "monitor_index": index
            if index is not None
            else controller_settings.window.monitor_index,
        }
    )
    return controller_settings.model_copy(update={"window": updated})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="engine.app", description=__doc__)
    parser.add_argument("--dev", action="store_true", help=f"load the UI from {DEV_URL}")
    parser.add_argument("--simulate", action="store_true", help="no game: simulated sessions")
    parser.add_argument("--dry-run", action="store_true", help="log keys instead of pressing")
    parser.add_argument("--no-hotkeys", action="store_true", help="do not grab F9/F10/F12")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")

    if not args.dev and not (UI_DIST / "index.html").is_file():
        print(
            "the UI is not built yet: run `npm --prefix ui run build`, or use --dev",
            file=sys.stderr,
        )
        return 2

    capture.ensure_dpi_awareness()
    controller = build_controller(simulate=args.simulate, dry_run=args.dry_run)
    settings = controller.settings
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(controller, enable_hotkeys=not args.no_hotkeys),
            host=settings.server_host,
            port=settings.server_port,
            log_level="warning",
        )
    )
    thread = threading.Thread(target=server.run, name="engine-server", daemon=True)
    thread.start()
    deadline = time.monotonic() + STARTUP_TIMEOUT_S
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            print(
                f"the engine did not start on port {settings.server_port} "
                "(is another copy already running?)",
                file=sys.stderr,
            )
            return 3
        time.sleep(0.05)

    url = DEV_URL if args.dev else f"http://{settings.server_host}:{settings.server_port}/"
    placement = place_window(settings.window, capture.list_monitors())
    logger.info("opening the window at %s", placement)
    window = webview.create_window(
        TITLE,
        url,
        x=placement.x,
        y=placement.y,
        width=placement.width,
        height=placement.height,
        min_size=MIN_SIZE,
        on_top=settings.window.always_on_top,
        background_color=BACKGROUND,
    )

    # "Always on top" applies at once; everything else in Settings waits for the next launch.
    previous = controller.on_settings_changed

    def on_settings(updated: cfg.Settings) -> None:
        if previous is not None:
            previous(updated)
        window.on_top = updated.window.always_on_top

    controller.on_settings_changed = on_settings

    def on_closing() -> None:
        try:
            controller.settings = remember(window, controller.settings)
            cfg.save_settings(controller.settings)
        except Exception:  # a failed save must not keep the window from closing
            logger.exception("could not remember the window position")

    window.events.closing += on_closing
    try:
        webview.start()
    finally:
        server.should_exit = True
        thread.join(5.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
