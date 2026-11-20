# AFK for WoW Forever

Fishes for you in World of Warcraft Forever. It listens to the game for the splash of a
bite, reels in, and casts again; a control panel on your second monitor shows what it hears
and lets you set everything up. The full specification is in `docs/PLAN.md`.

> Automating the game is against Blizzard's terms of service and can cost you the account.
> Whether and where to use this is your decision.

## What you need

- Windows 10 2004 or later (Windows 11 recommended), with WoW Forever (`Wow.exe`).
  macOS is not supported yet; the port is planned in `docs/MACOS_PORT.md`.
- [uv](https://docs.astral.sh/uv/) and Node.js 22 or later.

```bash
uv sync
npm --prefix ui install
npm --prefix ui run build
```

## Start it

```bash
uv run python -m engine.app
```

The control panel opens on your second screen. Press **Start fishing** with your character
at the water and a fishing pole equipped; WoW comes to the front and the bot takes over.

These keys work while WoW is in front, and WoW never sees them:

| Key | Does |
| --- | --- |
| F9 | Start or stop |
| F10 | Pause or resume |
| F12 | Emergency stop: lets go of every key at once |

Clicking out of WoW pauses the bot by itself, so keys never land in another window. You
can change the keys under **Settings**.

Other ways to run it:

```bash
uv run python -m engine.app --simulate   # no game: watch a simulated session
uv run python -m engine.server           # no window: control it from the hotkeys alone
uv run python -m engine.run --minutes 30 # headless, in the console
```

**Practice mode** (Settings, or `--dry-run`) does everything except press keys: every key
it would press is written to the log instead.

## Setting up each mode

Choose the mode under **Mode & keys**.

### A. One-button addon (recommended)

Your fishing addon casts with one key and reels in when you press it again; the bot presses
that key.

1. Enable your one-button fishing addon and bind its key (the default here is `]`).
2. Turn on **Auto Loot** (Interface → Controls).
3. Set the same key as **Fishing key** under Mode & keys.

### C. Interact key, no addon

1. Bind Fishing to an action bar key and set it as **Cast key**.
2. Turn on **Enable Interact Key** (Options → Controls) and bind it; set it as **Interact key**.
3. In chat: `/console SoftTargetInteractRange 30`.
4. Turn on **Auto Loot**, and have no target selected while fishing.

### B. Right-click the bobber

Needs bobber vision, which is not built yet. The settings are there, but a session in
mode B refuses to start.

### Sound, for every mode

The bot hears the bite, so: game **Sound Effects on**, **Music off**. Ambience may stay on.
It listens to WoW only, never to Discord or other apps. If you want the bot to keep
working while another window is in front for a moment, turn on **Sound in Background**.

## FishBridge addon (optional)

FishBridge lets the bot see what it cannot hear: it stops when your bags are full, pauses
when something attacks you, follows your lure's real timer, and recasts as soon as a cast
comes back empty instead of waiting the full 30 seconds.

```bash
uv run python -m tools.install_addon
```

With the game running, this copies `addon/FishBridge` into the game's `Interface/AddOns`.
Then `/reload` in game. It draws four small coloured squares in the very top-left corner of
the screen; that is how it talks to the bot, so keep that corner visible (Alt+Z hides it,
and the bot then fishes as if the addon were not there). While fishing, the Dashboard shows
what it reports.

## Calibrating a new spot

The defaults were measured in WoW Forever and work at most spots. For a new one, open
**Calibration** and walk through the six steps: pick the WoW window, set the search area,
show the bobber, let the bot hear the spot's background sound for twenty seconds, let it
time three casts, then five real test casts. Each step shows what it measured and changes
the profile only when you accept it. Save the result as a profile per spot under
**Profiles**.

## When something is off

- **It casts but never hooks.** Detection → Sensitivity lower, or redo Calibration's sound
  step. Check WoW's sound is on.
- **It hooks when nothing bit.** Sensitivity higher. Waves, rain and other players' spells
  at busy spots are the usual cause.
- **"Paused: N misses in a row".** Several casts with no bite heard: same checks as above.
- **To see exactly what the detector hears**, fish by hand with
  `uv run python -m tools.listen --bot` and watch for BITE lines.

## For development

`CLAUDE.md` has the conventions. In short:

```bash
uv run pytest                    # all tests; none need the game
uv run ruff check .
npm --prefix ui run dev          # UI with hot reload, against:
uv run python -m engine.server --simulate --no-hotkeys
```
