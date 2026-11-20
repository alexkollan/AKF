"""Live splash detector: listen to WoW and print BITE the moment a fish bites.

Fish by hand while this runs. Every splash it hears is printed with how far over the noise
floor it was, so you can see whether the detector catches the bites and nothing else —
before the state machine ever presses a key. Nothing is sent to the game.

    uv run python -m tools.listen                  # WoW only (process loopback)
    uv run python -m tools.listen --ratio 5        # stricter
    uv run python -m tools.listen --endpoint       # whole output device instead

The meter shows the in-band level (``#``), the noise floor (``:``) and the trigger
threshold (``|``). In-game: Sound Effects on, Music off. Ambience can stay on — the floor
follows it — but the quieter it is, the further a splash stands out.

The detector is armed the whole time here, so it also reacts to the cast whoosh and the
bobber landing. In the bot, the state machine only arms it after the bobber has landed.
"""

from __future__ import annotations

import argparse
import logging
import sys

from engine import audio as audio_mod
from engine import capture, clock
from engine.config import AudioDetectorConfig

METER_WIDTH = 50
METER_MIN_DB = -80.0
METER_MAX_DB = 0.0
REFRESH_S = 0.1

logger = logging.getLogger("listen")


def build_parser() -> argparse.ArgumentParser:
    defaults = AudioDetectorConfig()
    parser = argparse.ArgumentParser(prog="tools.listen", description=__doc__)
    parser.add_argument("--process", default=None, help="exact process name, e.g. Wow.exe")
    parser.add_argument("--pid", type=int, default=None, help="listen to this pid directly")
    parser.add_argument("--endpoint", action="store_true", help="hear the whole output device")
    parser.add_argument("--audio-device", type=int, default=None, help="endpoint device index")
    parser.add_argument(
        "--ratio",
        type=float,
        default=defaults.trigger_ratio,
        help=f"trigger ratio over the floor (default {defaults.trigger_ratio})",
    )
    parser.add_argument(
        "--floor-min",
        type=float,
        default=defaults.floor_min_dbfs,
        help=f"floor clamp in dBFS (default {defaults.floor_min_dbfs})",
    )
    parser.add_argument(
        "--band",
        type=int,
        nargs=2,
        metavar=("LOW", "HIGH"),
        default=defaults.band_hz,
        help="analysis band in Hz",
    )
    parser.add_argument(
        "--seconds", type=float, default=0.0, help="stop after this long (0: never)"
    )
    return parser


def meter(level: audio_mod.DetectorLevel) -> str:
    """One text line: level bar, floor and threshold marks, numbers."""

    def column(db: float) -> int:
        span = METER_MAX_DB - METER_MIN_DB
        ratio = (min(max(db, METER_MIN_DB), METER_MAX_DB) - METER_MIN_DB) / span
        return min(METER_WIDTH - 1, int(ratio * METER_WIDTH))

    cells = ["-"] * METER_WIDTH
    for index in range(column(level.level_db) + 1):
        cells[index] = "#"
    cells[column(level.floor_db)] = ":"
    cells[column(level.threshold_db)] = "|"
    return (
        f"[{''.join(cells)}] level {level.level_db:6.1f}  floor {level.floor_db:6.1f}  "
        f"thr {level.threshold_db:6.1f} dB"
    )


def resolve_pid(args: argparse.Namespace) -> tuple[int | None, str]:
    if args.endpoint:
        return None, "whole output device"
    if args.pid is not None:
        return args.pid, f"pid {args.pid}"
    window = capture.find_game_window(args.process)
    return window.pid, f"{window.process_name} (pid {window.pid})"


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)

    try:
        pid, target = resolve_pid(args)
        config = AudioDetectorConfig(
            trigger_ratio=args.ratio, floor_min_dbfs=args.floor_min, band_hz=tuple(args.band)
        )
        source = audio_mod.open_audio_source(pid, device_index=args.audio_device)
    except (capture.WindowNotFoundError, audio_mod.AudioError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    detector = audio_mod.SplashDetector(config, source.sample_rate)
    detector.arm()

    print(f"listening to : {target}")
    print(f"source       : {source.name}")
    over_db = audio_mod.to_dbfs(config.trigger_ratio)
    low, high = config.band_hz
    print(
        f"detector     : ratio {config.trigger_ratio} (+{over_db:.1f} dB), "
        f"floor >= {config.floor_min_dbfs} dBFS, band {low}-{high} Hz"
    )
    print("Ctrl+C to stop\n")

    bites = 0
    peak_db = -120.0
    try:
        source.start()
        started = clock.now()
        last_draw = 0.0
        while args.seconds <= 0 or clock.now() - started < args.seconds:
            chunk = source.read(timeout=0.2)
            if chunk is None:
                continue
            event = detector.feed(chunk)
            level = detector.last
            if level is not None:
                peak_db = max(peak_db, level.level_db)
            if event is not None:
                bites += 1
                sys.stdout.write("\r" + " " * (METER_WIDTH + 60) + "\r")
                print(
                    f"BITE #{bites:<3} at {event.t:8.2f}s   {event.level_db:6.1f} dB, "
                    f"+{audio_mod.to_dbfs(event.ratio):.1f} dB over the floor"
                )
            if level is not None and chunk.t - last_draw >= REFRESH_S:
                last_draw = chunk.t
                sys.stdout.write("\r" + meter(level))
                sys.stdout.flush()
    except KeyboardInterrupt:
        pass
    finally:
        source.close()

    print(
        f"\n\n{bites} bite(s) heard, loudest window {peak_db:.1f} dBFS, "
        f"{source.dropped} chunk(s) dropped"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
