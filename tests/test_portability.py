"""The portable core must import and run on an OS without a native backend.

A subprocess makes every Windows- and macOS-only library unimportable, selects the
placeholder backend, then imports the engine, the service and the tools and runs a
simulated session. If any portable module grows a top-level ``import win32gui`` or
``import Quartz`` again, this fails on every OS, long before it reaches the other one.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from engine.audio import AudioError
from engine.capture import CaptureError
from engine.input import InputController, InputError
from engine.native.unsupported import UnsupportedPlatform

ROOT = Path(__file__).resolve().parents[1]

SCRIPT = textwrap.dedent(
    """
    import importlib.abc
    import sys

    BLOCKED = {"win32api", "win32con", "win32gui", "win32process", "pywintypes",
               "dxcam", "comtypes", "pyaudiowpatch",
               # the macOS backend's pyobjc frameworks
               "objc", "AppKit", "Foundation", "Quartz", "ApplicationServices",
               "ScreenCaptureKit", "CoreMedia", "pynput"}

    class Block(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in BLOCKED:
                raise ImportError(f"{name} is Windows-only")
            return None

    sys.meta_path.insert(0, Block())

    # Faking sys.platform would break the stdlib (urllib reads it), so pick the backend
    # that any non-Windows OS gets today directly.
    from engine import native
    from engine.native.unsupported import UnsupportedPlatform

    backend = UnsupportedPlatform("darwin")
    native.current = lambda: backend

    import engine.app, engine.calibration, engine.clips, engine.controller  # noqa: F401
    import engine.run, engine.server, engine.session  # noqa: F401
    import tools.listen, tools.metrics, tools.permissions, tools.record_clip  # noqa: F401
    import tools.install_addon, tools.spike  # noqa: F401
    from engine.clock import SimClock
    from engine.config import Profile
    from engine.session import Session

    assert native.current().name == "darwin" and not native.current().supported
    clock = SimClock()
    session = Session(Profile(), simulate=True, clock_fn=clock.now, sleep_fn=clock.sleep)
    stats = session.run(duration_s=600)
    assert stats.catches > 0, stats
    print("ok", stats.catches)
    """
)


def test_portable_core_runs_without_windows_libraries():
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("ok")


@pytest.fixture
def unsupported(monkeypatch):
    backend = UnsupportedPlatform("darwin")
    monkeypatch.setattr("engine.native.current", lambda: backend)
    return backend


def test_game_facing_calls_fail_with_the_usual_error_types(unsupported):
    from engine import audio, capture

    with pytest.raises(CaptureError, match="not implemented on darwin"):
        capture.find_game_window()
    with pytest.raises(AudioError, match="not implemented on darwin"):
        audio.open_audio_source(1234)
    with pytest.raises(InputError, match="not implemented on darwin"):
        InputController().tap("1")


def test_dry_run_and_hotkeys_still_work(unsupported):
    from engine.hotkeys import GlobalHotkeys

    controller = InputController(dry_run=True)
    controller.tap("]")
    assert [a.kind for a in controller.actions] == ["key_down", "key_up"]

    hotkeys = GlobalHotkeys({"f9": lambda: None})
    hotkeys.start()
    hotkeys.stop()
