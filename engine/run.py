"""Headless fishing session from the command line.

    uv run python -m engine.run --dry-run            # listen to WoW, log keys, press nothing
    uv run python -m engine.run                      # fish for real
    uv run python -m engine.run --simulate --fast    # no game at all, 30 min in a second
    uv run python -m engine.run --profile "STV coast" --minutes 60

``--profile`` takes a JSON file or the name of a saved profile; without it the defaults
are used (mode A on ``]``). While it runs, two global hotkeys work even with WoW in front,
and neither reaches the game:

===========  ===============================================
  F10        pause / resume  (Settings.hotkeys.pause)
  F12        emergency stop  (Settings.hotkeys.emergency_stop)
===========  ===============================================

Clicking out of WoW pauses the bot by itself: keys must never land in another window.
Ctrl+C in this console stops it too.
"""

from __future__ import annotations

import argparse
import logging
import random
import sys
import time
from pathlib import Path

from engine import clock
from engine.config import Mode, Profile, list_profiles, load_profile, load_settings
from engine.fsm import FsmEvent, Status
from engine.hotkeys import GlobalHotkeys
from engine.session import Session, SessionError

COUNTDOWN_S = 3

logger = logging.getLogger("run")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="engine.run", description=__doc__)
    parser.add_argument("--profile", default=None, help="JSON file or saved profile name")
    parser.add_argument("--mode", choices=[mode.value for mode in Mode], help="override the mode")
    parser.add_argument("--dry-run", action="store_true", help="log every key, press nothing")
    parser.add_argument("--simulate", action="store_true", help="no game: simulated bites")
    parser.add_argument("--fast", action="store_true", help="--simulate on a fast clock")
    parser.add_argument("--minutes", type=float, default=None, help="stop after this long")
    parser.add_argument("--seed", type=int, default=None, help="random seed (simulation)")
    parser.add_argument("--quiet", action="store_true", help="print state changes only")
    return parser


def resolve_profile(spec: str | None) -> Profile:
    if spec is None:
        return Profile()
    path = Path(spec)
    if path.is_file():
        return Profile.model_validate_json(path.read_text(encoding="utf-8"))
    if spec in list_profiles():
        return load_profile(spec)
    saved = ", ".join(list_profiles()) or "none"
    msg = f"no profile file or saved profile named {spec!r} (saved: {saved})"
    raise SessionError(msg)


class Printer:
    def __init__(self, *, quiet: bool) -> None:
        self.quiet = quiet
        self.origin: float | None = None

    def __call__(self, event: FsmEvent) -> None:
        if self.origin is None:
            self.origin = event.t
        if self.quiet and event.kind != "state":
            return
        if event.kind == "state" and event.state is not None and not self.quiet:
            # Every cast walks RECOVER -> CAST -> WATCH -> HOOK; print only what matters.
            if event.status is Status.RUNNING:
                return
        marker = {"warn": "!", "error": "x"}.get(event.level, " ")
        print(f"{event.t - self.origin:8.1f}s {marker} {event.message}", flush=True)


def summarise(session: Session, now: float) -> str:
    snap = session.stats.snapshot(now)
    minutes = snap.elapsed_ms / 60000
    reactions = [record.reaction_s for record in session.stats.log]
    reaction = (
        f", hook {sum(reactions) / len(reactions) * 1000:.0f} ms after the bite"
        if reactions
        else ""
    )
    return (
        f"{minutes:.1f} min: {snap.casts} casts, {snap.catches} catches, {snap.misses} misses "
        f"({snap.success_rate:.0%}), {snap.catches_per_hour:.0f}/h, "
        f"{snap.lures_applied} lures{reaction}"
    )


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)
    if args.fast and not args.simulate:
        print("--fast only works with --simulate", file=sys.stderr)
        return 2

    try:
        profile = resolve_profile(args.profile)
    except (SessionError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.mode:
        profile = profile.model_copy(update={"mode": Mode(args.mode)})
    if args.minutes is not None:
        stop = profile.stop.model_copy(update={"max_duration_min": args.minutes})
        profile = profile.model_copy(update={"stop": stop})
    if args.simulate:
        logging.getLogger("engine.input").setLevel(logging.WARNING)  # dry-run noise

    sim = clock.SimClock() if args.fast else None
    printer = Printer(quiet=args.quiet)
    session = Session(
        profile,
        dry_run=args.dry_run,
        simulate=args.simulate,
        clock_fn=sim.now if sim else clock.now,
        sleep_fn=sim.sleep if sim else time.sleep,
        rng=random.Random(args.seed),
        on_event=printer,
    )

    settings = load_settings()
    hotkeys = None
    if not args.fast:
        hotkeys = GlobalHotkeys(
            {
                settings.hotkeys.pause: lambda: session.request("toggle_pause"),
                settings.hotkeys.emergency_stop: lambda: session.request("emergency_stop"),
            }
        )

    keys = profile.keys
    hook = {Mode.SINGLE_BUTTON: keys.fish_key, Mode.INTERACT_KEY: keys.interact_key}.get(
        profile.mode, "right-click"
    )
    cast = keys.fish_key if profile.mode is Mode.SINGLE_BUTTON else keys.cast_key
    kind = (
        "simulation" if args.simulate else ("DRY RUN - no keys pressed" if args.dry_run else "LIVE")
    )
    print(f"profile : {profile.name}  mode {profile.mode.value}  cast '{cast}'  hook '{hook}'")
    print(
        f"timing  : listen {profile.timing.land_delay_ms} ms after the cast, "
        f"give up after {profile.timing.max_wait_ms / 1000:g} s, "
        f"pause after {profile.stop.consecutive_misses} misses in a row"
    )
    print(f"run     : {kind}")
    if hotkeys is not None:
        pause_key, stop_key = settings.hotkeys.pause, settings.hotkeys.emergency_stop
        print(f"hotkeys : {pause_key} pause/resume, {stop_key} emergency stop")
    if not args.simulate and not args.dry_run:
        print(f"\nstarting in {COUNTDOWN_S}s - WoW will be brought to the front")
        time.sleep(COUNTDOWN_S)
    print()

    try:
        if hotkeys is not None:
            hotkeys.start()
        session.run()
    except SessionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        if hotkeys is not None:
            hotkeys.stop()

    now = sim.now() if sim else clock.now()
    print()
    if session.fsm.reason:
        print(f"ended   : {session.fsm.reason}")
    print(f"result  : {summarise(session, now)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
