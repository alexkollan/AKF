# macOS port

Goal: WoW Forever running natively on a Mac, with AFK driving it from the same Mac. This
file is the hand-over from the Windows work; read it after `CLAUDE.md` and `docs/PLAN.md`.

**Answered:** WoW Forever has a native macOS client (no Wine): the executable is
`/Applications/World of Warcraft/_forever_/World of Warcraft.app/Contents/MacOS/World of Warcraft`,
started by the launcher with `-launcherlogin -uid wowf`. Its windows' owner name is `Wow`;
the engine names the process after the executable, `World of Warcraft`, which is now in
`DEFAULT_WOW_PROCESSES`. A profile saying `Wow.exe` still finds it (a known client name falls
back to the other known clients when absent).

## Status on the Mac (2026-11-21, MacBook Air, Apple silicon, macOS 26.2)

The backend is `engine/native/macos/` and is selected for `darwin`. Measured against the live
client:

| Area | Result |
| --- | --- |
| Window discovery | found by process name, title readable with Screen Recording; also when WoW is in another Space |
| Geometry | points; Retina scale 2.0; fullscreen-windowed client = whole display (1470x956 pt) |
| Capture (ScreenCaptureKit) | 2940x1912 native frames, ROI crop matches the client crop (mean diff 4.2) |
| Capture rate | 38 fps on a 400x300-pt ROI, below the 60 fps target: ScreenCaptureKit only sends a frame when the game draws one, so this is likely the game's frame rate. Irrelevant for mode A; revisit with bobber vision |
| Focus | `focus_window` brings WoW (and its Space) to the front via `AXFrontmost`; `has_focus` costs 0.15 ms |
| Game audio | ScreenCaptureKit filtered to WoW: 48 kHz stereo float32, 20 ms buffers, continuous (silence included) |
| Audio isolation | a system sound played by another process: -53 dBFS in the WoW-only stream (just the game's ambience) vs -19 dBFS in the all-apps stream |
| Audio timestamps | presentation times are on `perf_counter`'s clock: lag 38 ms median, 52 ms max, no drift over 10 s, nothing re-stamped |
| Hotkey tap | starts with Accessibility + Input Monitoring; sees every press, swallows when asked (tested with a synthetic F19) |
| Swallowing from WoW | **does not work**: fn+F10/F12 paused and stopped the bot *and* opened bags. The tap removes the event from the Quartz stream (a listen-only tap after it sees nothing), but WoW links GameController and IOKit and reads the physical keyboard from HID, beneath every event tap. Only a virtual HID driver (Karabiner-style) could hide a key from it. On the Mac the bot's hotkeys (and the recorder's marker keys) must be keys WoW has no binding for |
| Keys in game | `tools.spike --key b`: Feign Death cast (seen in the capture 0.4 s later). `CGEventPost` keycodes reach the game |
| F10/F12 in game | `engine.run --dry-run` with WoW in front: F10 paused, F12 emergency-stopped. Needs fn held (or "standard function keys" on); without it the keys are volume/mute and never reach the tap |
| FishBridge | not installed on this Mac yet (`tools.install_addon` finds `_forever_/Interface/AddOns`) |
| First live session | `engine.run`, mode A on `]`, Windows defaults untouched: 7 casts, 6 bites heard (+12.4 to +13.9 dB), 6 hooked, 0 misses, 0 false bites; bites 14.5-26.8 s after the cast; hook 633 ms after the bite. Stopped with fn+F12 |
| Bite detection tuning | **pending**: record Mac clips (`tools.record_clip`, 20+ casts), `tools.replay --metrics/--sweep`; one bite came 26.8 s after the cast, past the 25.9 s seen on Windows, so watch `max_wait_ms` (30 s) |

Decisions taken (also in `CLAUDE.md`):

- **Coordinates are points, frames are native pixels.** `MonitorInfo.scale` relates them (2.0 on
  Retina, 1.0 on Windows). Input, window bounds and ScreenCaptureKit all speak points, and the
  global point space is continuous across mixed-scale monitors, which pixels are not. `mss` is
  switched off its nominal 1x mode so both capture paths deliver the same pixels.
- **FishBridge cells may be 4 or 8 captured pixels** (the game renders at native resolution or
  at half and is upscaled), so `bridge.decode` tries cell sizes 4, 8 and 2, and the reader grabs
  a corner big enough for the largest. The strip validates itself, so a wrong size never decodes.
- **Hotkeys use a Quartz event tap of our own, not pynput:** pynput's macOS keyboard listener
  calls the Text Input Sources API off the main thread, which recent macOS aborts on.
- **Focus goes through Accessibility:** `NSWorkspace.frontmostApplication` is only refreshed by
  the main run loop, which the engine does not run, and plain activation may be declined since
  macOS 14.
- **The endpoint fallback is ScreenCaptureKit over every app** (no BlackHole needed): one
  pseudo-device, "Every app".
- **Permissions** belong to the app running the engine (the Claude app or Terminal while
  developing). `tools.permissions [--request]` checks them, plus the function-key setting.

## Where it stands

The engine was split so that everything that talks to the operating system sits behind one
interface, `engine.native.Platform`:

```
engine/native/
  __init__.py        the Platform protocol and current(): picks the backend for sys.platform
  unsupported.py     any OS without a backend (macOS today): portable parts work, the rest
                     raises CaptureError / AudioError / InputError with a clear message
  windows/           the original, verified-in-game engine
    display.py       DPI awareness, monitors, windows, process path, dxcam
    keyboard_mouse.py SendInput scancodes, cursor, focus
    wasapi.py        endpoint loopback (PyAudioWPatch) and the source chooser
    process_loopback.py  Wow.exe-only audio via COM
    hotkeys.py       low-level keyboard hook (pynput win32_event_filter)
```

`engine.capture`, `engine.input`, `engine.audio` and `engine.hotkeys` are the portable
facade; callers did not change. Key names are portable (`engine.keys`): profiles say `"]"`,
`"f9"`, `"space"`, and each backend maps the canonical name to its own codes.
`pyproject.toml` installs the Windows-only packages only on Windows.

`tests/test_portability.py` imports the engine, the service and the tools with every
Windows library blocked and runs a simulated session on the unsupported backend, so a stray
`import win32gui` in a portable module fails the suite on Windows already.

### What should work on a Mac right after cloning

```bash
uv sync
npm --prefix ui install && npm --prefix ui run build
uv run pytest                                   # Windows backend tests are skipped
uv run python -m engine.run --simulate --fast --minutes 30
uv run python -m engine.server --simulate --no-hotkeys   # + npm --prefix ui run dev
uv run python -m engine.app --simulate --no-hotkeys
```

Check these first. Anything failing here is a portability bug in the core, not missing Mac
code. (`tools.replay` playback wants PyAudio: `brew install portaudio`, then add `pyaudio`
for darwin, or switch playback to `sounddevice`.)

## The work: `engine/native/macos/` (the original plan, now implemented)

Add a `MacPlatform` that satisfies `Platform`, and return it from `native.current()` for
`darwin`. Add its packages to `pyproject.toml` with `sys_platform == 'darwin'` markers
(`pyobjc-framework-Quartz`, `-Cocoa`, `-ScreenCaptureKit`, `-CoreMedia`, `-AVFoundation`).
Suggested order, easiest and most verifiable first: display → input and focus → hotkeys →
audio. Area by area:

### Display and window discovery

- Windows: `CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID)`;
  each entry has `kCGWindowNumber` (use as `WindowInfo.hwnd`), `kCGWindowOwnerPID`,
  `kCGWindowOwnerName`, `kCGWindowName` (needs Screen Recording permission), `kCGWindowBounds`.
- The game's process name differs from `Wow.exe` (likely `World of Warcraft`); find it with
  `tools.spike --list` once that works and add it to `DEFAULT_WOW_PROCESSES`.
- Monitors: `CGGetActiveDisplayList` + `CGDisplayBounds`, or `NSScreen.screens()` (work area
  = `visibleFrame`, but NSScreen is bottom-left origin; Quartz display bounds are top-left,
  y down, like Windows).
- **Points vs pixels is the big decision.** Quartz reports geometry in points; a Retina
  screen has 2 pixels per point. The engine's rule is "physical pixels, relative to the
  client area". Either convert everything to pixels in the backend (scale by
  `NSScreen.backingScaleFactor`) and back to points for input, or define geometry in points
  on macOS and scale captured frames. Pick one and write it down in `CLAUDE.md`
  ("Coordinates"). ROIs are stored as fractions, so profiles survive either choice.
- Client area: `kCGWindowBounds` includes the title bar in windowed mode. Run WoW fullscreen
  (windowed) to sidestep this at first.
- `process_path`: `NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
  .executableURL()` (or `bundleURL()`). `tools.install_addon` assumes the exe sits next to
  `Interface/`; on a Mac the executable is inside `World of Warcraft.app`, so derive the
  `_forever_/Interface/AddOns` path from the bundle, or just pass `--dir`.

### Capture

- `mss` already works on macOS (portable fallback, used by the bridge too). Note that its
  boxes are in points and the image comes back in pixels on Retina; `bridge.py` reads 4x4
  cells and must account for that.
- Fast path (`open_native_capture`): ScreenCaptureKit `SCStream` over the game window or a
  display region, delivering BGRA `CVPixelBuffer`s. Optional for mode A (audio only); needed
  later for bobber vision.
- Permission: **Screen Recording** for the terminal (or the app) running the engine.

### Input and focus

- Keys: `CGEventCreateKeyboardEvent(None, keycode, down)` + `CGEventPost(kCGHIDEventTap, …)`.
  Use the `kVK_ANSI_*` virtual keycodes, which are positional like Windows scancodes, so a
  non-US layout still presses the bound key. Build a table from every name in
  `engine.keys.NAMED_KEYS` plus single characters; `check_key` raises `InputError` for names
  the Mac has no key for (`insert`, F20-F24).
- Modifiers: post the modifier key events *and* set `CGEventSetFlags` on the key/mouse event
  (`kCGEventFlagMaskShift` for shift-right-click looting).
- Mouse: `CGEventCreateMouseEvent` with `kCGEventMouseMoved`, `kCGEventRightMouseDown/Up`
  etc. Coordinates are global points. Games sometimes need `kCGEventRightMouseDragged` or
  the click state field (`kCGMouseEventClickState`) set; check what WoW accepts.
- Focus: `has_focus` = `NSWorkspace.sharedWorkspace().frontmostApplication()
  .processIdentifier() == window.pid`; `focus_window` = `NSRunningApplication …
  .activateWithOptions_(NSApplicationActivateIgnoringOtherApps)`.
- Permission: **Accessibility** for synthetic input.
- Verify like M0: `tools.spike --key b` must trigger Feign Death in game.

### Global hotkeys

- pynput on macOS runs a Quartz event tap; its `darwin_intercept(event_type, event)`
  callback can **swallow** an event by returning `None`. Compare the event's keycode
  (`CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)`) with the table above, fire
  once per press (ignore `kCGKeyboardEventAutorepeat`), and swallow only when `swallow=True`.
  Mirror `engine/native/windows/hotkeys.py`.
- F-keys on Mac keyboards send media functions unless "Use F1, F2, etc. keys as standard
  function keys" is on (or fn is held). Check that before debugging the hook.
- Permission: **Input Monitoring** (plus Accessibility to swallow).

### Audio (the hard part)

- Target: the equivalent of process loopback, meaning WoW's audio only. ScreenCaptureKit
  (macOS 13+) does this: an `SCContentFilter` that includes only WoW's
  `SCRunningApplication`, `SCStreamConfiguration.capturesAudio = True`,
  `excludesCurrentProcessAudio = True`, and an `SCStreamOutput` for `.audio`. Buffers are
  float32 `CMSampleBuffer`s; convert to int16 `(frames, channels)` `AudioChunk`s.
- Alternative on macOS 14.2+: Core Audio process taps (`AudioHardwareCreateProcessTap`).
- Last resort: a virtual output device (BlackHole) + PyAudio, the equivalent of endpoint
  loopback (hears every app, follows system volume).
- Timestamps: chunk `t` must be on `engine.clock.now()`'s timeline. On macOS
  `time.perf_counter()` is based on `mach_absolute_time`, and a buffer's presentation
  timestamp is in host time, so `CMTimeGetSeconds(pts)` should be directly comparable.
  **Verify that** (log both side by side) before trusting it, as was done for QPC on Windows.
- Like process loopback, expect true digital silence between sounds; the detector's floor
  clamp already handles it.
- Permission: Screen Recording covers ScreenCaptureKit audio.

### Packaging and permissions

Permissions attach to the binary that asks: the terminal while developing, the app later.
Packaging is still deferred by the user's choice; do not add it unasked.

## Re-tuning on the Mac

The detector and timings were measured on Windows. Record new clips on the Mac
(`tools.record_clip`, at least 20 casts), then `tools.replay --metrics` and `--sweep` to
check `trigger_ratio`; then `tools.listen --bot` live, aiming for the Windows result
(14/14 bites, no false ones). Land and bite timings are game behaviour and should carry over.

## Context carried over from the Windows sessions

The agent's notes from the Windows machine do not travel with the repo, so the parts that
matter here are copied here:

- **Language:** chat with the user in Greek; everything written to disk (code, comments,
  docs, commits) is in English.
- **WoW Forever is Mainline (retail)**, client 1.60.1, TOC Interface 16001, with retail's
  secret-value API limits. Never assume Classic behaviour.
- **User's setup:** mode A (one-button fishing addon) on the `]` key; mode C (interact key)
  must stay available as a fallback. Auto Loot on. In-game bindings: `B` = Feign Death
  (hunter, handy for input tests), F8-F12 open bags (so the bot's F9/F10/F12 and the
  recorder's F7-F10/F12 must be swallowed), F7 free. A large green nameplate addon sits in
  screen centre (relevant to bobber vision later). Discord runs alongside the game, which
  is why per-app audio capture matters.
- **Audio detection validated on Windows (2026-11-20):** `tools.listen --bot` while fishing
  by hand: 14/14 bites, 0 false, defaults ratio 3.5, floor clamp -60 dBFS, band 300-4000 Hz.
  Landing sound 0.6-1.3 s after the cast key (not always audible); bite 6.8-25.9 s after the
  cast key; the splash often has 2-3 onsets ~0.8 s apart (disarm after the first); splash
  peaks -25 to -36 dBFS, onsets only +11 to +16 dB over a ~-63 dBFS floor.
- In-game verification is done by the user: write the tool, say exactly how to run it, and
  wait for the result.
