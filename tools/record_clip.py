"""Record real fishing into a labelled fixture: ROI video + loopback audio + markers.

Run this, then fish by hand and tap a key whenever something happens. Nothing is sent to
the game — the recorder only watches and listens.

    uv run python -m tools.record_clip --list-devices
    uv run python -m tools.record_clip --process WowClassic.exe --name stv-night --tags night,sea

While it runs:

===========  ==================================================================
  F7         a cast started
  F8         the bobber landed
  F9         a fish bit  (tap it the moment you hear the splash)
  F10 / Esc  stop and save
  F12        stop and throw the recording away
===========  ==================================================================

Markers can be fixed afterwards: ``markers.json`` inside the clip is plain JSON, and
``tools.replay`` can nudge a marker onto the exact frame.

**About the bite marker.** F9 records when *you* reacted, which is a quarter of a second
or so after the splash itself. That is expected and it is why M3 reports a signed latency
against the marker rather than assuming the marker is the truth.

Target for M3: at least 20 casts across 2-3 spots, day and night, sea and lake. Record a
few clips rather than one long one, and tag each with where and when it was taken.
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from pynput import keyboard

from engine import audio as audio_mod
from engine import capture
from engine.clips import (
    MARKER_BITE,
    MARKER_CAST,
    MARKER_LANDED,
    Clip,
    ClipGeometry,
    ClipWriter,
)
from engine.config import CaptureBackend, RoiFraction

DEFAULT_OUT = Path("tests/fixtures")

MARKER_KEYS: dict[keyboard.Key, tuple[str, str]] = {
    keyboard.Key.f7: (MARKER_CAST, "cast"),
    keyboard.Key.f8: (MARKER_LANDED, "bobber landed"),
    keyboard.Key.f9: (MARKER_BITE, "bite"),
}

logger = logging.getLogger("record_clip")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tools.record_clip", description=__doc__)
    parser.add_argument(
        "--list-devices", action="store_true", help="list loopback devices and exit"
    )
    parser.add_argument("--process", default=None, help="exact process name, e.g. Wow.exe")
    parser.add_argument("--title", default="", help="only match windows whose title contains this")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="fixtures directory")
    parser.add_argument("--name", default="", help="label appended to the timestamp")
    parser.add_argument("--tags", default="", help="comma-separated, e.g. night,sea")
    parser.add_argument("--notes", default="", help="free text stored in meta.json")
    parser.add_argument(
        "--roi",
        nargs=4,
        type=float,
        metavar=("X", "Y", "W", "H"),
        default=None,
        help="ROI as fractions of the client area (default: centre 60%%)",
    )
    parser.add_argument("--fps", type=int, default=30, help="capture rate (default 30)")
    parser.add_argument(
        "--format",
        choices=["ffv1", "png"],
        default="ffv1",
        help="ffv1 is lossless video (default); png is a lossless frame sequence",
    )
    parser.add_argument(
        "--backend",
        choices=[item.value for item in CaptureBackend],
        default=CaptureBackend.AUTO.value,
    )
    parser.add_argument("--audio-device", type=int, default=None, help="loopback device index")
    parser.add_argument("--no-audio", action="store_true", help="record video only")
    parser.add_argument(
        "--max-minutes", type=float, default=20.0, help="stop automatically after this long"
    )
    return parser


class MarkerListener:
    """Global hotkeys for the marker keys, timestamped the instant they are pressed."""

    def __init__(self, t0: float) -> None:
        self.t0 = t0
        self.pending: deque[tuple[float, str, str]] = deque()
        self.stop_requested = False
        self.discard_requested = False
        self._lock = threading.Lock()
        self._listener = keyboard.Listener(on_press=self._on_press)

    def start(self) -> None:
        self._listener.start()

    def stop(self) -> None:
        self._listener.stop()

    def drain(self) -> list[tuple[float, str, str]]:
        with self._lock:
            items = list(self.pending)
            self.pending.clear()
        return items

    def _on_press(self, key: keyboard.Key | keyboard.KeyCode | None) -> None:
        # Timestamp first: everything after this costs milliseconds we do not want in the
        # marker, since the whole point is to pin down when something happened.
        now = time.monotonic() - self.t0
        if key in MARKER_KEYS:
            kind, label = MARKER_KEYS[key]  # type: ignore[index]
            with self._lock:
                self.pending.append((now, kind, label))
        elif key in (keyboard.Key.f10, keyboard.Key.esc):
            self.stop_requested = True
        elif key is keyboard.Key.f12:
            self.discard_requested = True


def print_devices() -> None:
    devices = audio_mod.list_loopback_devices()
    if not devices:
        print("no WASAPI loopback devices found")
        return
    print("loopback devices:")
    for device in devices:
        print(f"  {device}")


def clip_directory(root: Path, name: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = f"-{name}" if name else ""
    return root / f"{stamp}{suffix}"


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)

    if args.list_devices:
        print_devices()
        return 0

    capture.ensure_dpi_awareness()
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

    device: audio_mod.AudioDevice | None = None
    if not args.no_audio:
        try:
            device = audio_mod.resolve_device(args.audio_device)
        except audio_mod.AudioError as exc:
            print(f"error: {exc}", file=sys.stderr)
            print("record video only with --no-audio", file=sys.stderr)
            return 4

    target = clip_directory(args.out, args.name)
    geometry = ClipGeometry(
        client_rect=window.client_rect.as_tuple(),
        roi_screen=roi.as_tuple(),
        roi_fraction=fraction,
        monitor_device=window.monitor.device,
        process_name=window.process_name,
        window_title=window.title,
    )

    print(f"window   : {window.title!r} ({window.process_name})")
    print(f"roi      : {roi.width}x{roi.height} at {roi.as_tuple()}")
    print(f"audio    : {device if device else 'disabled'}")
    print(f"clip     : {target}")
    print(f"format   : {args.format} @ {args.fps} fps")
    print()
    print("F7 cast   F8 landed   F9 bite   F10/Esc save   F12 discard")
    print()

    writer = ClipWriter(
        target,
        codec=args.format,
        fps_target=args.fps,
        audio_rate=device.sample_rate if device else 48000,
        audio_channels=device.channels if device else 2,
        audio_device_name=device.name if device else "",
        with_audio=device is not None,
        geometry=geometry,
        tags=[tag.strip() for tag in args.tags.split(",") if tag.strip()],
        notes=args.notes,
    )

    try:
        counts = _record(writer, window, roi, device, args)
    except KeyboardInterrupt:
        print("\ninterrupted")
        counts = None
    except Exception:
        writer.discard()
        raise

    if counts is None:
        writer.discard()
        print("discarded")
        return 1

    clip = writer.close()
    _report(clip, counts)
    return 0


def _record(
    writer: ClipWriter,
    window: capture.WindowInfo,
    roi: capture.Rect,
    device: audio_mod.AudioDevice | None,
    args: argparse.Namespace,
) -> dict[str, int] | None:
    """The recording loop. Returns marker counts, or ``None`` when the user discarded."""
    backend = CaptureBackend(args.backend)
    deadline = args.max_minutes * 60
    counts: dict[str, int] = {MARKER_CAST: 0, MARKER_LANDED: 0, MARKER_BITE: 0}

    t0 = time.monotonic()
    listener = MarkerListener(t0)
    listener.start()

    stream: audio_mod.LoopbackStream | None = None
    try:
        if device is not None:
            stream = audio_mod.LoopbackStream(device, chunk_ms=20)
            stream.start()
            writer.set_audio_start(stream.start_monotonic - t0)

        with capture.capture_session(
            roi, window.monitor, backend=backend, target_fps=args.fps
        ) as source:
            last_status = 0.0
            while True:
                if listener.discard_requested:
                    return None
                if listener.stop_requested:
                    break

                frame = source.grab()
                now = time.monotonic() - t0
                if frame is not None:
                    writer.add_frame(now, frame)

                if stream is not None:
                    for chunk in stream.drain():
                        writer.add_audio(chunk.samples, at=chunk.t)

                for timestamp, kind, label in listener.drain():
                    writer.add_marker(timestamp, kind)
                    counts[kind] = counts.get(kind, 0) + 1
                    print(f"  {timestamp:7.2f}s  {label}")

                if now - last_status >= 2.0:
                    last_status = now
                    _status(now, writer, counts, stream)

                if now >= deadline:
                    print(f"\nreached --max-minutes ({args.max_minutes})")
                    break
    finally:
        listener.stop()
        if stream is not None:
            stream.close()

    return counts


def _status(
    now: float,
    writer: ClipWriter,
    counts: dict[str, int],
    stream: audio_mod.LoopbackStream | None,
) -> None:
    frames = len(writer.frame_times)
    fps = frames / now if now > 0 else 0.0
    dropped = f"  audio-dropped {stream.dropped}" if stream and stream.dropped else ""
    sys.stdout.write(
        f"\r  {now:6.1f}s  {frames:6d} frames ({fps:4.1f} fps)  "
        f"casts {counts[MARKER_CAST]}  bites {counts[MARKER_BITE]}{dropped}   "
    )
    sys.stdout.flush()


def _report(clip: Clip, counts: dict[str, int]) -> None:
    size_mb = sum(f.stat().st_size for f in clip.path.rglob("*") if f.is_file()) / 1024 / 1024
    casts = clip.casts()
    print("\n")
    print(f"saved    : {clip.path}")
    print(
        f"duration : {clip.meta.duration:.1f}s, {clip.meta.video.frame_count} frames "
        f"({clip.meta.video.frame_count / max(clip.meta.duration, 1e-6):.1f} fps)"
    )
    print(
        f"audio    : {clip.meta.audio.frame_count} samples @ {clip.meta.audio.sample_rate} Hz, "
        f"offset {clip.meta.audio.start_offset * 1000:+.0f} ms"
    )
    print(
        f"markers  : {counts[MARKER_CAST]} cast, {counts[MARKER_LANDED]} landed, "
        f"{counts[MARKER_BITE]} bite  ->  {len(casts)} cast segments"
    )
    print(f"size     : {size_mb:.1f} MiB")
    print()
    print(f"review it with:  uv run python -m tools.replay {clip.path}")


if __name__ == "__main__":
    raise SystemExit(main())
