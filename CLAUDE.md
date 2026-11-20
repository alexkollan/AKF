# CLAUDE.md — AFK

A fishing bot for WoW Forever: a Python engine that sees, hears and acts, plus a React UI
that runs on the second monitor. The full specification is `docs/PLAN.md` — read it before
touching anything; this file only records the decisions and conventions that follow from it.

## What this is

The engine watches the WoW client with screen capture and WASAPI loopback audio, decides
when a fish bites, and drives the game with synthetic key and mouse input. The game's API
deliberately hides the bite, so detection is image- or sound-based only. The UI is a thin
client: every decision lives in the engine, so the bot keeps fishing with the UI closed.

The engine core (capture, input, service, UI shell) is the base for a later grind bot. When
a choice is between "specific to fishing" and "reusable", prefer reusable — but do not build
grind-bot features now.

## Stack

| Layer | Choice |
| --- | --- |
| Runtime | Python 3.12 on Windows 10/11 (macOS port in progress, `docs/MACOS_PORT.md`), managed by `uv` |
| Screen capture | `dxcam` (DXGI), `mss` fallback |
| Vision | `opencv-python` + `numpy` |
| Audio | `PyAudioWPatch` (WASAPI loopback) |
| Input | `SendInput` scancodes via `ctypes`, `pywin32` for windows and focus |
| Global hotkeys | `pynput` |
| Service | `FastAPI` + one WebSocket on `uvicorn`, `pydantic` for config |
| UI | React 19 + Vite + TypeScript, Tailwind v4, shadcn/ui, Zustand, Recharts |
| Desktop shell | `pywebview` loading the UI from localhost |
| Packaging | `PyInstaller`, one-folder |
| Tests | `pytest` over recorded clips (frames + wav) |

## Layout

```
engine/
  capture.py        geometry, WoW window + monitor discovery, capture (native fast path, mss)
  input.py          InputController: keys, mouse move/click, dry run, release-all; focus
  keys.py           portable key names; backends map them to native codes
  audio.py          AudioChunk/AudioSource, source selection, wav files, splash detector
  native/           the only OS-specific code: the Platform protocol and its backends
    windows/        dxcam, SendInput, WASAPI + process loopback (COM), keyboard hook
    unsupported.py  any other OS until it has a backend: portable parts work, rest errors
  clips.py          recorded-clip format: writer, loader, frame reader, cast segments
  clock.py          the one high-resolution clock (QPC) everything is stamped with
  vision/bobber.py  find bobber: HSV mask + template + blob scoring
  vision/bite.py    bite: drop/disappear/motion in ROI
  fsm.py            fishing state machine, modes A/B/C (pure: time and signals come in)
  session.py        wires the FSM to audio, input and focus (or a simulated world)
  run.py            headless CLI: python -m engine.run [--dry-run] [--simulate --fast]
  app.py            desktop app: server + pywebview window on the remembered monitor
  hotkeys.py        global hotkeys, swallowed before they reach WoW (or just watched)
  bridge.py         decodes the FishBridge addon's colour strip (bags, combat, lure, channel)
  calibration.py    the calibration wizard's steps and the analyses behind its suggestions
  lures.py          lure timer + apply sequence
  config.py         pydantic models, profiles (JSON)
  stats.py          session stats, catch log
  controller.py     single point of control: sessions, profiles, settings, event queue
  protocol.py       WebSocket wire format (pydantic), mirrored by ui/src/lib/protocol.ts
  server.py         FastAPI + one WebSocket on 127.0.0.1:8777, global hotkeys
ui/                 Vite + React + TS: screens/, components/ (ui/ = shadcn), lib/, store/
tools/
  record_clip.py    records frames + audio of real casts
  replay.py         plays clips, edits markers, --metrics/--sweep score the detector
  metrics.py        per-cast scoring of the bite detector against markers (M3)
  listen.py         live splash detector: prints BITE while you fish by hand
  spike.py          hardware smoke test (capture + input) against a live client
  install_addon.py  copies addon/FishBridge into the game's Interface/AddOns
tests/fixtures/     recorded clips, labelled
addon/FishBridge/   the optional Lua addon (Interface 16001)
docs/PLAN.md        the specification; milestones M0-M7
README.md           setup and use, per mode
```

## Commands

```bash
uv sync                      # install/refresh the Python environment
uv run pytest                # tests (fixture-backed; none need the game)
uv run ruff check .          # lint
uv run ruff format .         # format
uv run python -m tools.spike # capture + input smoke test, needs WoW running
uv run python -m tools.record_clip --name <spot> --tags night,sea  # record fixtures
uv run python -m tools.replay [clip]  # list fixtures, or play one with markers
uv run python -m tools.replay --metrics   # score the bite detector on all fixtures
uv run python -m tools.listen  # live bite detection on WoW audio, nothing sent to the game
uv run python -m engine.run --dry-run  # headless session: listens, logs keys, presses nothing
uv run python -m engine.run    # fish for real (F10 pause, F12 emergency stop)
uv run python -m engine.run --simulate --fast --minutes 30  # no game, half an hour in a second
uv run python -m engine.server  # engine service for the UI: ws://127.0.0.1:8777/ws, F9/F10/F12
npm --prefix ui run dev      # UI dev server on http://localhost:5173
npm --prefix ui run build    # typecheck + production build
uv run python -m engine.app  # the desktop app (needs the UI built); --dev uses Vite, --simulate no game
uv run python -m engine.server --simulate --no-hotkeys  # engine for UI work, game closed
```

`uv run` is the only supported way to run Python here: there is no global interpreter on
this machine, the project pins 3.12 in `.python-version`, and `[tool.uv] package = false`
means the repo root is on `sys.path` rather than installed.

## Conventions

**Types.** Every function and method is annotated; `ruff`'s `ANN` rules enforce it. Config
and anything crossing the WebSocket is a pydantic model, never a loose dict.

**No blocking calls in the asyncio loop.** `engine/server.py` owns the event loop. Capture,
audio and input all block or need their own threads, so they run on worker threads and talk
to the loop through queues or `run_in_executor`. Never call `time.sleep`, a dxcam grab, or a
`SendInput` burst from a coroutine.

**Detectors are pure and replayable.** Every detector is a class with a config dataclass (or
pydantic model) and a `feed(frame | audio_chunk) -> Event | None` method. It owns no capture,
no clock and no I/O: timestamps arrive with the data. This is a hard rule, because it is what
makes the detection testable:

> **Detectors must be testable on recorded fixtures with the game closed.** Any test that
> needs WoW running is marked `@pytest.mark.needs_game` and is not part of the normal run.

**Fixtures.** A clip is a directory: `meta.json`, `frames.json` (one timestamp per frame),
`markers.json` (hand-editable ground truth), `video.mkv` (FFV1) or `frames/*.png`, and
`audio.wav`. Video is always lossless, because HSV thresholds and frame diffs are exactly what
a lossy codec corrupts. Load clips through `engine.clips`, never by reading the files directly.
Media files are git-ignored (they run to hundreds of MB); move to Git LFS if they need sharing.

**Platforms.** Only `engine/native/` talks to the operating system. `engine.native.Platform`
lists what a backend provides (monitors and windows, a fast capture, keys and mouse, focus, game
audio, global hotkeys); `engine.native.current()` picks one lazily, so importing any other
module never loads `pywin32`, `dxcam`, `comtypes` or `PyAudioWPatch`. Code outside `native/`
uses the facades (`engine.capture`, `engine.input`, `engine.audio`, `engine.hotkeys`), names
keys by their portable names (`engine.keys`), and never imports a backend directly.
Windows-only packages carry a `sys_platform == 'win32'` marker in `pyproject.toml`.
`tests/test_portability.py` blocks every Windows library and runs a simulated session; keep it
green. The macOS backend is the next step: `docs/MACOS_PORT.md`.

**Clock.** Everything that gets compared in time — frames, audio chunks, markers, input
actions — is stamped with `engine.clock.now()` (`perf_counter`, QPC, 100 ns). On Windows
`time.monotonic()` is `GetTickCount64` with 15.6 ms steps; never use it, or `time.time()`, for
anything timed. WASAPI packet timestamps are QPC too (`clock.from_qpc`), so audio, video and
hotkeys share one timeline.

**Audio source.** The default is **process loopback** (`engine.native.windows.process_loopback`): it captures
`Wow.exe` alone, so Discord, music or a browser can never fake a bite. It is driven through COM
with `comtypes` on its own MTA thread, and its chunks carry exact QPC capture times.
`engine.audio.open_audio_source(pid)` picks it and falls back to **endpoint loopback**
(`wasapi.LoopbackStream`, everything the device plays) only when unavailable or asked for.

**Audio timing on the endpoint fallback.** Endpoint loopback delivers *nothing* while nothing
plays — no callbacks, no zero buffers. Its chunk timestamps follow the sample count while audio
flows and re-anchor to the clock after a gap (`engine.audio.stamp_chunk`), and anything that
writes audio places it by timestamp (`ClipWriter.add_audio(..., at=t)`). Never derive time from
"samples received so far". Its level also follows the Windows volume; process loopback does
not, but it delivers true digital silence between sounds. Either way, detectors work on ratios
to a noise floor with a floor clamp (`floor_min_dbfs`), never on absolute amplitude.

**Bite detection.** `SplashDetector` fires on the rising edge of in-band RMS over a rolling
median floor, only while armed. The state machine arms it after the bobber has landed and
disarms it at the first detection. Every window feeds the median (excluding loud ones once made
the floor unlearnable); tune `trigger_ratio` from fixtures with `tools.replay --sweep`, not by
feel.

**State machine.** `engine.fsm.FishingFSM` is pure: `tick(now, Signals)` in, `GameActions`
calls and `FsmEvent`s out. It never reads a clock or touches capture, audio or input — keep it
that way, because the 30-minute-per-mode soak tests (`tests/test_session.py`) run it on
`clock.SimClock` with `session.SimulatedWorld`. Timing defaults in `TimingConfig` are measured
in-game (landing 0.6-1.3 s, bites 6.8-25.9 s after the cast key); change them from data. Profile
changes made while running take effect at the next cast. Mode B and pixel fusion are refused
for a real game until bobber vision exists.

**Coordinates.** All geometry is relative to the WoW *client area*, in physical pixels. The
process is per-monitor DPI aware (`SetProcessDpiAwarenessContext`), so two monitors with
different scaling still land a right-click in the right place. Profiles store ROIs as
fractions of the client area so they survive a resolution change; conversion to pixels happens
once per capture session.

**Focus.** `SendInput` goes to whatever window has focus. The engine focuses WoW on start,
auto-pauses when WoW loses focus, and the UI window never steals focus while a session runs.

**Timing.** Fixed delays are jittered (`TimingConfig.jitter_ms`). Anything measurable — land
delay, channel duration — is measured in the calibration wizard and stored in the profile, not
hard-coded.

**Safety valves.** `--dry-run` logs every key and mouse action instead of sending it; use it
for any change to the FSM or input path. Emergency stop must release every held key and return
in under 100 ms.

**Errors.** Fail loudly in the engine's own code (raise), degrade gracefully on the hardware
edge (missing audio device, dxcam unavailable → fall back to `mss` and say so in the log).
`extra="forbid"` on config models means a typo in a hand-edited profile is an error, not a
silently ignored key.

## UI conventions

- Look: night water. Tokens in `ui/src/index.css` keep shadcn/ui names but carry the palette
  (abyss, depth, shoal, foam, mist, lagoon); Archivo Variable with its width axis does the
  typographic work (condensed numerals, expanded state words). Lagoon teal is the only accent;
  red is never decoration.
- Components: shadcn/ui, style `base-nova`, which sits on Base UI, not Radix. Add more with
  `npx shadcn@latest add <name>` from `ui/`, then check them: the generated `data-horizontal:`
  variants did not match Base UI 1.9's `data-orientation` attribute (sliders had zero width)
  and were rewritten to `data-[orientation=…]:`.
- `ui/src/lib/config.ts` is the TypeScript view of `engine/config.py`; keep it in step. Every
  settings field goes through `components/fields.tsx` (`Field` gives the tooltip with help and
  default, plus a mark when changed; `Section` gives "Reset to defaults" from the engine's
  `defaults`). Copy is plain language from the player's side, sentence case.
- Effects are always block-bodied. `scrollIntoView` now returns a Promise in Chromium, and an
  expression-bodied `useEffect` returning it crashed the whole tree on unmount.
- `npm run build` is `tsc -b && vite build`. Plain `tsc` checks nothing here (the root
  tsconfig only holds references), which hid type errors until M6.
- The WebSocket contract lives twice: `engine/protocol.py` (pydantic, validated) and
  `ui/src/lib/protocol.ts` (types). They mirror each other field for field — camelCase on the
  wire, except `profile`/`settings` payloads, which keep the config models' snake_case. Change
  both sides in the same commit.
- `ui/src/lib/ws.ts` keeps exactly one live socket; every handler ignores a socket that is no
  longer current (StrictMode double-mounts effects in development).
- The sidebar is data-driven (`SCREENS` in `ui/src/App.tsx`) so the grind bot's screens can be
  appended without a rewrite.
- State colours mean one thing each: green running, amber paused, red stopped or error, grey
  idle. The Dashboard fits a 1080p screen with no scrolling.

## Working on this repo

Chat with the user in Greek; everything written to disk (code, comments, docs, commits) is in
English.

Milestones run in order (`docs/PLAN.md`, "milestones"), and detection (M2-M3) lands before the
UI because that is where the risk is. Anything that needs the game open — calibration, clip
recording, in-game verification of a milestone's acceptance criteria — is done by the user, not
by the agent. Write the tool, say exactly how to run it, and wait for the result.

## Engine service

`engine.server` is the long-running process the UI talks to. Commands from the WebSocket and
the global hotkeys (F9 start/stop, F10 pause, F12 emergency stop — all swallowed before WoW
sees them) go to the same `Controller` methods. The controller runs one `Session` at a time on
a worker thread and turns everything into wire events on a thread-safe queue that the server
pumps every 50 ms; a client that connects late gets a snapshot first. Only the UI's origins
(and origin-less local clients) may connect. Emergency stop is a request the session applies
at its next tick, so it lands in well under 100 ms (`tests/test_controller.py` measures it).
Server tests run a real uvicorn on a free port with the `websockets` client.

## FishBridge and calibration

The addon paints four 4x4-pixel cells in the top-left corner; `engine/bridge.py` decodes them.
The layout is written down at the top of `FishBridge.lua` and `bridge.encode` mirrors it — change
both together. Every reading has its own valid bit and is taken inside `pcall`, doing all its
comparing there, because a secret value in WoW Forever errors the moment it is compared outside.
Missing, unreadable or frozen strips mean "no addon": every rule that needs it is skipped. Rules:
combat pauses at once; full bags stop between casts; the lure timer follows the game (with a grace
period after our own application); a channel that ended without a bite is a miss right away.
"Pole equipped" is shown, never enforced, until it is confirmed in game.

Calibration steps that listen or press keys run one at a time on the `Calibrator`'s thread and
never overlap a fishing session. Suggestions are computed by pure functions
(`analyse_ambient`, `suggest_land_delay_ms`) and applied only when the user accepts them. The
ambient analysis uses the same clamped floor the detector does, and refuses digital silence (a
muted or backgrounded WoW) instead of suggesting a number.

Packaging (PyInstaller) is deferred by the user's choice; do not add it unasked.
