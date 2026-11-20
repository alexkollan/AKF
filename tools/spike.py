"""Hardware smoke test for M1: find the client, focus it, press a key, capture an ROI.

This is the one tool that needs the game running. It answers the three questions M1 asks:

1. can the engine find the WoW window and does a synthetic key actually reach the game;
2. does the captured ROI line up exactly with the client area, on either monitor;
3. does capture sustain the target frame rate on a 400x300 ROI.

Run it with the game open and visible::

    uv run python -m tools.spike --list          # what windows are open
    uv run python -m tools.spike --key b         # focus WoW and open the bags
    uv run python -m tools.spike --dry-run       # capture only, send nothing

Output lands in ``./out``: the ROI crop, the whole client area, and the client with the
ROI outlined, so the alignment can be checked by eye.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from engine import capture
from engine import input as game_input
from engine.config import CaptureBackend, RoiFraction

OUT_DIR = Path("out")
BENCH_ROI = (400, 300)
BENCH_TARGET_FPS = 60

logger = logging.getLogger("spike")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tools.spike", description=__doc__)
    parser.add_argument("--list", action="store_true", help="list open windows and exit")
    parser.add_argument("--process", default=None, help="exact process name, e.g. Wow.exe")
    parser.add_argument("--title", default="", help="only match windows whose title contains this")
    parser.add_argument("--key", default="b", help="key to press in-game (default: b, bags)")
    parser.add_argument("--no-focus", action="store_true", help="do not bring WoW to the front")
    parser.add_argument("--no-input", action="store_true", help="capture only, press nothing")
    parser.add_argument("--dry-run", action="store_true", help="log input instead of sending it")
    parser.add_argument(
        "--roi",
        nargs=4,
        type=float,
        metavar=("X", "Y", "W", "H"),
        default=None,
        help="ROI as fractions of the client area (default: centre 60%%)",
    )
    parser.add_argument(
        "--backend",
        choices=[item.value for item in CaptureBackend],
        default=CaptureBackend.AUTO.value,
    )
    parser.add_argument("--fps", type=int, default=60, help="target capture fps")
    parser.add_argument("--bench", type=float, default=2.0, help="benchmark seconds (0 to skip)")
    return parser


def print_windows() -> None:
    monitors = capture.list_monitors()
    print(f"{len(monitors)} monitor(s):")
    for monitor in monitors:
        tag = " (primary)" if monitor.is_primary else ""
        print(
            f"  [{monitor.index}] {monitor.device}{tag} "
            f"{monitor.rect.width}x{monitor.rect.height} at "
            f"({monitor.rect.left}, {monitor.rect.top})"
        )
    print("\nopen windows:")
    for window in capture.list_windows():
        print(
            f"  {window.process_name:<28} mon {window.monitor.index}  "
            f"client {window.client_rect.width}x{window.client_rect.height}  "
            f"{window.title[:60]!r}"
        )


def describe(window: capture.WindowInfo, roi: capture.Rect) -> None:
    client = window.client_rect
    print(f"window      : {window.title!r} (hwnd {window.hwnd}, pid {window.pid})")
    print(f"process     : {window.process_name}")
    print(
        f"monitor     : [{window.monitor.index}] {window.monitor.device} "
        f"{window.monitor.rect.width}x{window.monitor.rect.height} "
        f"at ({window.monitor.rect.left}, {window.monitor.rect.top})"
    )
    print(f"window rect : {window.window_rect.as_tuple()}")
    print(f"client rect : {client.as_tuple()}  ({client.width}x{client.height})")
    print(
        f"roi         : {roi.as_tuple()}  ({roi.width}x{roi.height})  "
        f"client-relative {window.screen_to_client(roi.left, roi.top)}"
    )


def save(image: np.ndarray, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), image):
        msg = f"could not write {path}"
        raise OSError(msg)
    print(f"saved {path}  ({image.shape[1]}x{image.shape[0]})")


def grab_once(
    region: capture.Rect,
    monitor: capture.MonitorInfo,
    backend: CaptureBackend,
    fps: int,
) -> tuple[np.ndarray, str]:
    """One frame from a freshly opened capture, with the backend that served it."""
    with capture.capture_session(region, monitor, backend=backend, target_fps=fps) as source:
        deadline = time.perf_counter() + 3.0
        while time.perf_counter() < deadline:
            frame = source.grab()
            if frame is not None:
                return frame, source.backend_name
    msg = f"no frame arrived within 3s from region {region}"
    raise capture.CaptureError(msg)


def run_benchmark(
    window: capture.WindowInfo, backend: CaptureBackend, fps: int, seconds: float
) -> None:
    """Measure the acceptance criterion: >= 60 fps on a 400x300 ROI."""
    width, height = BENCH_ROI
    left, top = window.client_rect.left, window.client_rect.top
    region = capture.Rect(left, top, left + width, top + height).clamped_to(window.client_rect)

    with capture.capture_session(region, window.monitor, backend=backend, target_fps=fps) as src:
        src.grab()  # warm up: the first dxcam frame costs more than the rest
        measured = capture.measure_fps(src, seconds)
        verdict = "PASS" if measured >= BENCH_TARGET_FPS else "BELOW TARGET"
        print(
            f"capture     : {src.backend_name} {region.width}x{region.height} "
            f"-> {measured:.1f} fps (acceptance >= {BENCH_TARGET_FPS}) {verdict}"
        )


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)

    capture.ensure_dpi_awareness()

    if args.list:
        print_windows()
        return 0

    try:
        window = capture.find_game_window(args.process, args.title)
    except capture.WindowNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    fraction = (
        RoiFraction(x=args.roi[0], y=args.roi[1], w=args.roi[2], h=args.roi[3])
        if args.roi
        else RoiFraction()
    )
    roi = window.roi(fraction)
    describe(window, roi)

    backend = CaptureBackend(args.backend)
    controller = game_input.InputController(dry_run=args.dry_run)

    if not args.no_focus:
        focused = game_input.focus_window(window)
        print(f"focus       : {'ok' if focused else 'FAILED - the key will go elsewhere'}")
        if not focused and not args.dry_run:
            print("refusing to send input while WoW is not in front", file=sys.stderr)
            return 3

    if args.key and not args.no_input:
        controller.tap(args.key)
        print(f"input       : tapped {args.key!r}{' (dry run)' if args.dry_run else ''}")
        time.sleep(0.4)  # let the game draw whatever the key opened

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    roi_frame, backend_name = grab_once(roi, window.monitor, backend, args.fps)
    client_frame, _ = grab_once(window.client_rect, window.monitor, backend, args.fps)
    print(f"backend     : {backend_name}")

    outlined = client_frame.copy()
    rx, ry = window.screen_to_client(roi.left, roi.top)
    cv2.rectangle(outlined, (rx, ry), (rx + roi.width - 1, ry + roi.height - 1), (0, 0, 255), 2)

    save(roi_frame, OUT_DIR / f"spike-{stamp}-roi.png")
    save(client_frame, OUT_DIR / f"spike-{stamp}-client.png")
    save(outlined, OUT_DIR / f"spike-{stamp}-client-with-roi.png")

    # The ROI crop must equal the same region cut out of the full client capture; if the
    # two disagree, the DPI or client-origin maths is wrong.
    cut = client_frame[ry : ry + roi.height, rx : rx + roi.width]
    if cut.shape == roi_frame.shape:
        difference = float(np.mean(cv2.absdiff(cut, roi_frame)))
        print(
            f"alignment   : mean |roi - client crop| = {difference:.2f} "
            f"({'ok' if difference < 8 else 'CHECK - frames differ, animation or offset'})"
        )
    else:
        print(f"alignment   : shape mismatch {cut.shape} vs {roi_frame.shape} - OFFSET BUG")

    if args.bench > 0:
        run_benchmark(window, backend, args.fps, args.bench)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
