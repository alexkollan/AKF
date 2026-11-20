"""Live splash detector: listen to WoW and print BITE the moment a fish bites.

Fish by hand while this runs. Nothing is sent to the game — the fish key is only watched,
never pressed or blocked.

    uv run python -m tools.listen --bot            # hear it the way the bot will
    uv run python -m tools.listen                  # every splash-like sound, armed always
    uv run python -m tools.listen --ratio 5        # stricter
    uv run python -m tools.listen --endpoint       # whole output device instead of WoW

Each press of the fish key (``]`` by default) is printed as its own line, and every BITE
says how long after the last press it came, so the log reads as a timeline: cast, landing,
bite, loot.

**--bot** listens the way the state machine will. A press of the fish key (cast or loot)
stops listening; it starts again ``--arm-after`` seconds later, once the bobber has landed
and settled; the first BITE after that is the hook, and listening stops until the next
press. Sounds heard while not listening are printed as ``ignored`` so you can see what the
bot would have skipped. A fish that bites sooner than ``--arm-after`` is missed here too.

Without ``--bot`` the detector is armed the whole time and also reacts to the cast whoosh,
the bobber landing and the loot sound.

The meter shows the in-band level (``#``), the noise floor (``:``) and the trigger
threshold (``|``). In-game: Sound Effects on, Music off.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import threading

from engine import audio as audio_mod
from engine import capture, clock
from engine.config import AudioDetectorConfig, KeyBindings, TimingConfig
from engine.hotkeys import GlobalHotkeys
from engine.input import InputError, check_key

METER_MIN_DB = -80.0
METER_MAX_DB = 0.0
METER_MAX_WIDTH = 30
REFRESH_S = 0.1
#: Same delay the bot uses between the cast key and listening for the bite.
DEFAULT_ARM_AFTER_S = TimingConfig().land_delay_ms / 1000

logger = logging.getLogger("listen")


def build_parser() -> argparse.ArgumentParser:
    defaults = AudioDetectorConfig()
    parser = argparse.ArgumentParser(prog="tools.listen", description=__doc__)
    parser.add_argument("--process", default=None, help="exact process name, e.g. Wow.exe")
    parser.add_argument("--pid", type=int, default=None, help="listen to this pid directly")
    parser.add_argument("--endpoint", action="store_true", help="hear the whole output device")
    parser.add_argument("--audio-device", type=int, default=None, help="endpoint device index")
    parser.add_argument(
        "--bot", action="store_true", help="listen only when the bot would (see above)"
    )
    parser.add_argument(
        "--arm-after",
        type=float,
        default=DEFAULT_ARM_AFTER_S,
        help=f"--bot: seconds after a key press before listening (default {DEFAULT_ARM_AFTER_S})",
    )
    parser.add_argument(
        "--key",
        default=KeyBindings().fish_key,
        help=f"the fish key to watch (default {KeyBindings().fish_key})",
    )
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
    parser.add_argument("--no-meter", action="store_true", help="print only events")
    return parser


# --------------------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------------------


def console_width() -> int:
    return max(40, shutil.get_terminal_size((100, 20)).columns - 1)


def meter(level: audio_mod.DetectorLevel, width: int) -> str:
    """One status line that always fits in ``width`` characters.

    It is redrawn in place with ``\\r``, which only works while it stays on one physical
    line: a line that wraps turns every redraw into a new row of output.
    """
    numbers = (
        f" {level.level_db:5.0f} dB  floor {level.floor_db:4.0f}  thr {level.threshold_db:4.0f}"
    )
    bar_width = max(10, min(METER_MAX_WIDTH, width - len(numbers) - 2))

    def column(db: float) -> int:
        span = METER_MAX_DB - METER_MIN_DB
        ratio = (min(max(db, METER_MIN_DB), METER_MAX_DB) - METER_MIN_DB) / span
        return min(bar_width - 1, int(ratio * bar_width))

    cells = ["-"] * bar_width
    for index in range(column(level.level_db) + 1):
        cells[index] = "#"
    cells[column(level.floor_db)] = ":"
    cells[column(level.threshold_db)] = "|"
    return f"[{''.join(cells)}]{numbers}"[:width]


class Console:
    """Event lines scroll; the meter line is redrawn underneath them."""

    def __init__(self, *, show_meter: bool) -> None:
        self.show_meter = show_meter
        self.width = console_width()
        self._meter_shown = False

    def event(self, text: str) -> None:
        self._clear_meter()
        print(text[: self.width])

    def draw(self, level: audio_mod.DetectorLevel) -> None:
        if not self.show_meter:
            return
        sys.stdout.write("\r" + meter(level, self.width).ljust(self.width))
        sys.stdout.flush()
        self._meter_shown = True

    def _clear_meter(self) -> None:
        if self._meter_shown:
            sys.stdout.write("\r" + " " * self.width + "\r")
            self._meter_shown = False


# --------------------------------------------------------------------------------------
# key watching and bot-style gating
# --------------------------------------------------------------------------------------


class KeyGate:
    """When the bot would be listening, driven by presses of the fish key.

    Times are seconds on the audio stream's clock. A press (cast or loot) closes the gate;
    it opens ``arm_after`` seconds later; a bite closes it again until the next press.
    Before the first press it is open, so the tool is useful before you start fishing.
    """

    def __init__(self, arm_after: float) -> None:
        self.arm_after = arm_after
        self.last_press: float | None = None
        self._hooked = False

    def press(self, t: float) -> None:
        self.last_press = t
        self._hooked = False

    def hooked(self) -> None:
        self._hooked = True

    def is_open(self, t: float) -> bool:
        if self._hooked:
            return False
        return self.last_press is None or t - self.last_press >= self.arm_after

    def since_press(self, t: float) -> str:
        if self.last_press is None:
            return ""
        return f"{t - self.last_press:5.1f}s after key"


class KeyWatcher:
    """Timestamps presses of one key without blocking it — WoW still receives it."""

    def __init__(self, key_name: str, origin: float) -> None:
        self.key_name = key_name
        self.origin = origin
        self._pending: list[float] = []
        self._lock = threading.Lock()
        # One press per hold is the hook's job; auto-repeat never reaches pressed().
        self._hotkeys = GlobalHotkeys({key_name: self.pressed}, swallow=False)

    def start(self) -> None:
        self._hotkeys.start()

    def stop(self) -> None:
        self._hotkeys.stop()

    def drain(self) -> list[float]:
        with self._lock:
            presses, self._pending = self._pending, []
        return presses

    def pressed(self) -> None:
        now = clock.now() - self.origin
        with self._lock:
            self._pending.append(now)


def resolve_pid(args: argparse.Namespace) -> tuple[int | None, str]:
    if args.endpoint:
        return None, "whole output device"
    if args.pid is not None:
        return args.pid, f"pid {args.pid}"
    window = capture.find_game_window(args.process)
    return window.pid, f"{window.process_name} (pid {window.pid})"


# --------------------------------------------------------------------------------------
# main loop
# --------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)

    try:
        pid, target = resolve_pid(args)
        config = AudioDetectorConfig(
            trigger_ratio=args.ratio, floor_min_dbfs=args.floor_min, band_hz=tuple(args.band)
        )
        check_key(args.key)
        source = audio_mod.open_audio_source(pid, device_index=args.audio_device)
    except (capture.WindowNotFoundError, audio_mod.AudioError, InputError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # In --bot mode ``gated`` decides; ``shadow`` is always armed and only reports what the
    # bot skipped. Two detectors, so the skipped sounds cannot disturb the real decision.
    gated = audio_mod.SplashDetector(config, source.sample_rate)
    shadow = audio_mod.SplashDetector(config, source.sample_rate)
    shadow.arm()
    gate = KeyGate(args.arm_after)
    console = Console(show_meter=not args.no_meter)

    over_db = audio_mod.to_dbfs(config.trigger_ratio)
    low, high = config.band_hz
    print(f"listening to : {target}")
    print(f"source       : {source.name}")
    print(
        f"detector     : +{over_db:.1f} dB over the floor, floor >= {config.floor_min_dbfs} "
        f"dBFS, band {low}-{high} Hz"
    )
    if args.bot:
        print(f"mode         : bot - pauses on '{args.key}', listens {args.arm_after}s later")
    else:
        print(f"mode         : always listening; presses of '{args.key}' are shown")
    print("Ctrl+C to stop\n")

    bites = 0
    ignored = 0
    presses = 0
    peak_db = -120.0
    watcher: KeyWatcher | None = None
    try:
        source.start()
        watcher = KeyWatcher(args.key, source.start_time)
        watcher.start()
        started = clock.now()
        last_draw = 0.0
        while args.seconds <= 0 or clock.now() - started < args.seconds:
            for pressed_at in watcher.drain():
                presses += 1
                gate.press(pressed_at)
                console.event(f"  [{args.key}]  at {pressed_at:8.2f}s")

            chunk = source.read(timeout=0.2)
            if chunk is None:
                continue

            listening = gate.is_open(chunk.t) if args.bot else True
            if listening and not gated.armed:
                gated.arm()
            elif not listening and gated.armed:
                gated.disarm()

            event = gated.feed(chunk)
            heard = shadow.feed(chunk)
            level = gated.last
            if level is not None:
                peak_db = max(peak_db, level.level_db)

            if event is not None:
                bites += 1
                if args.bot:
                    gate.hooked()
                console.event(
                    f"BITE #{bites:<3} at {event.t:8.2f}s  {event.level_db:6.1f} dB  "
                    f"+{audio_mod.to_dbfs(event.ratio):4.1f} dB  {gate.since_press(event.t)}"
                )
            elif args.bot and heard is not None:
                ignored += 1
                console.event(
                    f"  ignored at {heard.t:8.2f}s  {heard.level_db:6.1f} dB  "
                    f"+{audio_mod.to_dbfs(heard.ratio):4.1f} dB  {gate.since_press(heard.t)}"
                )

            if level is not None and chunk.t - last_draw >= REFRESH_S:
                last_draw = chunk.t
                console.draw(level)
    except KeyboardInterrupt:
        pass
    finally:
        if watcher is not None:
            watcher.stop()
        source.close()

    console.event("")
    summary = f"{bites} bite(s)"
    if args.bot:
        summary += f", {ignored} sound(s) ignored"
    print(
        f"{summary}, {presses} press(es) of '{args.key}', loudest {peak_db:.1f} dBFS, "
        f"{source.dropped} chunk(s) dropped"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
