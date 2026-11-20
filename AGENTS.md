# AGENTS.md — fishbot

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
| Runtime | Python 3.12 on Windows 10/11, managed by `uv` |
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
  capture.py        dxcam/mss, WoW window + monitor discovery, DPI aware
  input.py          SendInput scancodes, mouse move/click, window focus
  audio.py          WASAPI loopback stream, splash detector
  clips.py          recorded-clip format: writer, loader, frame reader, cast segments
  vision/bobber.py  find bobber: HSV mask + template + blob scoring
  vision/bite.py    bite: drop/disappear/motion in ROI
  fsm.py            fishing state machine, modes A/B/C
  lures.py          lure timer + apply sequence
  config.py         pydantic models, profiles (JSON)
  stats.py          session stats, catch log
  server.py         FastAPI + WebSocket API
ui/                 Vite + React + TS
tools/
  record_clip.py    records frames + audio of real casts
  replay.py         runs detectors on recorded clips
  spike.py          hardware smoke test (capture + input) against a live client
tests/fixtures/     recorded clips, labelled
docs/PLAN.md        the specification; milestones M0-M7
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
npm --prefix ui run dev      # UI dev server on http://localhost:5173
npm --prefix ui run build    # typecheck + production build
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

**Audio timing.** WASAPI loopback delivers *nothing* while nothing plays — no callbacks, no
zero buffers. Chunk timestamps therefore follow the sample count while audio flows and
re-anchor to the wall clock after a gap (`engine.audio.stamp_chunk`), and anything that writes
audio places it by timestamp (`ClipWriter.add_audio(..., at=t)`). Never derive time from
"samples received so far". The captured level also follows the Windows output volume, so
detectors must work on ratios to a noise floor, never on absolute amplitude.

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

- Tokens in `ui/src/index.css` follow shadcn/ui names, so its components drop in unchanged.
- `ui/src/lib/protocol.ts` is the single source of truth for the WebSocket contract; the
  engine's pydantic models must match it. Change both sides in the same commit.
- The sidebar is data-driven (`SCREENS` in `ui/src/App.tsx`) so the grind bot's screens can be
  appended without a rewrite.
- State colours mean one thing each: green running, amber paused, red stopped or error, grey
  idle. The Dashboard fits a 1080p screen with no scrolling.

## Working on this repo

Milestones run in order (`docs/PLAN.md`, "milestones"), and detection (M2-M3) lands before the
UI because that is where the risk is. Anything that needs the game open — calibration, clip
recording, in-game verification of a milestone's acceptance criteria — is done by the user, not
by the agent. Write the tool, say exactly how to run it, and wait for the result.
