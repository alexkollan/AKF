"""Pydantic configuration models and profile storage.

A *profile* is everything that is tied to a fishing spot and a client setup: mode, key
bindings, lures, detection thresholds, ROI and stop conditions. Profiles live as JSON in
``%APPDATA%/AKF/profiles``. *Settings* are machine-wide and live next to them.

Every field has a default, so a fresh install starts from a working configuration and the
UI can offer "Reset to defaults" per screen.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------------------
# primitives
# --------------------------------------------------------------------------------------


class Mode(StrEnum):
    """How the bot hooks and loots a catch (docs/PLAN.md, "Τρόποι λειτουργίας")."""

    SINGLE_BUTTON = "A"  # one-key fishing addon: same key casts and hooks
    RIGHT_CLICK = "B"  # move the mouse onto the bobber and right-click
    INTERACT_KEY = "C"  # soft-target interact key, no addon needed


class FusionMode(StrEnum):
    """Which detectors must agree before a bite is accepted."""

    AUDIO = "audio"
    PIXEL = "pixel"
    AUDIO_OR_PIXEL = "audio_or_pixel"
    AUDIO_AND_PIXEL = "audio_and_pixel"


class CaptureBackend(StrEnum):
    DXCAM = "dxcam"
    MSS = "mss"
    AUTO = "auto"


class Model(BaseModel):
    """Base model: reject unknown keys so a typo in a hand-edited profile is loud."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


Fraction = Annotated[float, Field(ge=0.0, le=1.0)]


class RoiFraction(Model):
    """A rectangle expressed as fractions of the WoW client area.

    Stored relative so a profile survives a resolution change; the engine converts it to
    physical pixels against the current client rect every time capture starts.
    """

    x: Fraction = 0.2
    y: Fraction = 0.2
    w: Annotated[float, Field(gt=0.0, le=1.0)] = 0.6
    h: Annotated[float, Field(gt=0.0, le=1.0)] = 0.6

    @field_validator("w", "h")
    @classmethod
    def _non_degenerate(cls, value: float) -> float:
        if value <= 0.01:
            msg = "ROI side must be larger than 1% of the client area"
            raise ValueError(msg)
        return value


class HsvRange(Model):
    """Inclusive HSV bounds in OpenCV ranges: H 0-179, S 0-255, V 0-255."""

    h_min: Annotated[int, Field(ge=0, le=179)] = 0
    h_max: Annotated[int, Field(ge=0, le=179)] = 10
    s_min: Annotated[int, Field(ge=0, le=255)] = 120
    s_max: Annotated[int, Field(ge=0, le=255)] = 255
    v_min: Annotated[int, Field(ge=0, le=255)] = 90
    v_max: Annotated[int, Field(ge=0, le=255)] = 255


# The red feather wraps around H=0, so the red preset needs two ranges.
RED_FEATHER_PRESET: tuple[HsvRange, HsvRange] = (
    HsvRange(h_min=0, h_max=10, s_min=120, v_min=90),
    HsvRange(h_min=170, h_max=179, s_min=120, v_min=90),
)
BLUE_FEATHER_PRESET: tuple[HsvRange, ...] = (HsvRange(h_min=95, h_max=125, s_min=80, v_min=60),)


# --------------------------------------------------------------------------------------
# profile sections
# --------------------------------------------------------------------------------------


class KeyBindings(Model):
    """In-game keys the engine presses. Names are the ones `engine.input` understands."""

    fish_key: str = "]"  # mode A: cast and hook with the one-button addon key
    cast_key: str = "9"  # modes B and C: start the cast
    interact_key: str = "f"  # mode C: soft-target interact


class MouseOptions(Model):
    shift_right_click: bool = False  # needed when Auto Loot is off
    restore_cursor: bool = True  # put the cursor back where it was after the click
    move_steps: Annotated[int, Field(ge=1, le=40)] = 6  # a straight-line move, not a jump
    click_hold_ms: Annotated[int, Field(ge=5, le=200)] = 40


class CaptureConfig(Model):
    backend: CaptureBackend = CaptureBackend.AUTO
    target_fps: Annotated[int, Field(ge=10, le=240)] = 60
    process_name: str = "Wow.exe"  # WoW Forever runs on the Mainline client
    window_title_contains: str = ""  # extra filter when several clients run


class TimingConfig(Model):
    """Measured in the calibration wizard, not guessed.

    Defaults come from a live WoW Forever session (2026-11-20, `tools.listen --bot`,
    14 casts): the bobber's landing sound came 0.6-1.3 s after the cast key, and fish bit
    6.8-25.9 s after it — longer than the Classic-era 22 s channel this used to assume.
    """

    #: Cast key -> start listening for the bite. Landing is done by ~1.3 s; the rest is margin.
    land_delay_ms: Annotated[int, Field(ge=0, le=5000)] = 2000
    #: Cast key -> give up and recast. Bites were seen up to 25.9 s after the cast.
    max_wait_ms: Annotated[int, Field(ge=1000, le=60000)] = 30000
    #: Bite heard -> hook. A human beat; the game allows a few seconds, so this costs nothing.
    hook_delay_ms: Annotated[int, Field(ge=0, le=3000)] = 300
    #: Hook -> recast. Loot (auto loot) finishes within ~0.7 s of the hook in-game.
    post_hook_delay_ms: Annotated[int, Field(ge=0, le=5000)] = 1000
    between_casts_ms: Annotated[int, Field(ge=0, le=10000)] = 600
    jitter_ms: Annotated[int, Field(ge=0, le=2000)] = 180  # randomises the fixed delays


class AudioDetectorConfig(Model):
    #: Capture only Wow.exe (Windows 11 process loopback). Off: hear the whole output device.
    process_loopback: bool = True
    device_index: int | None = None  # endpoint fallback; None: the default output device
    chunk_ms: Annotated[int, Field(ge=5, le=100)] = 20  # analysis window
    noise_window_s: Annotated[float, Field(ge=0.5, le=30.0)] = 4.0
    trigger_ratio: Annotated[float, Field(ge=1.1, le=20.0)] = 3.5  # band RMS / noise floor
    #: The floor never counts as quieter than this. Process loopback delivers true digital
    #: silence between sounds, and a ratio over silence would fire on anything at all.
    floor_min_dbfs: Annotated[float, Field(ge=-120.0, le=0.0)] = -60.0
    min_gap_ms: Annotated[int, Field(ge=50, le=5000)] = 700  # refractory period
    use_template: bool = False  # cross-correlate against a recorded splash (needs fixtures)
    template_path: str | None = None
    band_hz: tuple[int, int] = (300, 4000)

    @field_validator("band_hz")
    @classmethod
    def _band_is_ordered(cls, value: tuple[int, int]) -> tuple[int, int]:
        low, high = value
        if not 0 <= low < high <= 24000:
            msg = f"band_hz must be 0 <= low < high <= 24000, got {value}"
            raise ValueError(msg)
        return value


class PixelDetectorConfig(Model):
    bobber_roi_px: Annotated[int, Field(ge=20, le=400)] = 90  # side of the watch window
    centroid_drop_px: Annotated[float, Field(ge=1.0, le=100.0)] = 6.0
    area_drop_ratio: Annotated[float, Field(ge=0.1, le=0.95)] = 0.5
    frame_diff_threshold: Annotated[float, Field(ge=0.0, le=255.0)] = 18.0
    consecutive_frames: Annotated[int, Field(ge=1, le=10)] = 2


class BobberDetectorConfig(Model):
    hsv_ranges: list[HsvRange] = Field(default_factory=lambda: list(RED_FEATHER_PRESET))
    min_blob_area: Annotated[int, Field(ge=1, le=10000)] = 12
    max_blob_area: Annotated[int, Field(ge=10, le=100000)] = 2500
    min_confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 0.45
    template_paths: list[str] = Field(default_factory=list)
    prefer_center: bool = True  # tie-break towards the middle of the search ROI


class DetectionConfig(Model):
    fusion: FusionMode = FusionMode.AUDIO
    search_roi: RoiFraction = Field(default_factory=RoiFraction)
    audio: AudioDetectorConfig = Field(default_factory=AudioDetectorConfig)
    pixel: PixelDetectorConfig = Field(default_factory=PixelDetectorConfig)
    bobber: BobberDetectorConfig = Field(default_factory=BobberDetectorConfig)


class Lure(Model):
    name: str = "Shiny Bauble"
    key: str = "7"  # a macro key: /use <lure> + /use 16
    duration_min: Annotated[float, Field(gt=0.0, le=120.0)] = 10.0
    cast_time_s: Annotated[float, Field(ge=0.0, le=30.0)] = 2.0
    priority: Annotated[int, Field(ge=0, le=100)] = 0  # higher wins
    enabled: bool = True


class LureConfig(Model):
    auto_apply: bool = False
    refresh_margin_s: Annotated[int, Field(ge=0, le=300)] = 15
    lures: list[Lure] = Field(default_factory=list)


class StopConditions(Model):
    max_duration_min: int | None = None
    max_catches: int | None = None
    consecutive_misses: Annotated[int, Field(ge=1, le=100)] = 5
    pause_on_focus_loss: bool = True
    stop_on_bags_full: bool = True  # needs the FishBridge addon (M7)
    pause_on_combat: bool = True  # needs the FishBridge addon (M7)


class ClientFingerprint(Model):
    """What the client looked like when this profile was calibrated.

    A mismatch means the vision thresholds are no longer trustworthy, so the UI asks for a
    re-calibration instead of silently misfiring.
    """

    client_width: int = 0
    client_height: int = 0
    ui_scale: float = 0.0
    monitor_name: str = ""


class Profile(Model):
    """Everything tied to one fishing spot and client setup."""

    name: str = "default"
    notes: str = ""
    mode: Mode = Mode.SINGLE_BUTTON
    keys: KeyBindings = Field(default_factory=KeyBindings)
    mouse: MouseOptions = Field(default_factory=MouseOptions)
    capture: CaptureConfig = Field(default_factory=CaptureConfig)
    timing: TimingConfig = Field(default_factory=TimingConfig)
    detection: DetectionConfig = Field(default_factory=DetectionConfig)
    lures: LureConfig = Field(default_factory=LureConfig)
    stop: StopConditions = Field(default_factory=StopConditions)
    fingerprint: ClientFingerprint = Field(default_factory=ClientFingerprint)
    calibrated_at: datetime | None = None

    def requires_bobber_vision(self) -> bool:
        """Mode B always needs the bobber located; the others only for pixel detection."""
        if self.mode is Mode.RIGHT_CLICK:
            return True
        return self.detection.fusion is not FusionMode.AUDIO


# --------------------------------------------------------------------------------------
# machine-wide settings
# --------------------------------------------------------------------------------------


class Hotkeys(Model):
    """Global hotkeys, from the OS's keyboard hook so they fire while WoW has focus."""

    start_stop: str = "<f9>"
    pause: str = "<f10>"
    emergency_stop: str = "<f12>"


class Notifications(Model):
    windows_toast: bool = True
    webhook_url: str | None = None


class WindowState(Model):
    monitor_index: int = 1  # 0 is the primary monitor; the UI opens on the second one
    x: int | None = None
    y: int | None = None
    width: int = 1280
    height: int = 860
    always_on_top: bool = False


class Settings(Model):
    active_profile: str = "default"
    server_host: str = "127.0.0.1"
    server_port: Annotated[int, Field(ge=1024, le=65535)] = 8777
    hotkeys: Hotkeys = Field(default_factory=Hotkeys)
    notifications: Notifications = Field(default_factory=Notifications)
    window: WindowState = Field(default_factory=WindowState)
    dry_run: bool = False  # log key/mouse actions instead of sending them


# --------------------------------------------------------------------------------------
# storage
# --------------------------------------------------------------------------------------

_SAFE_NAME = re.compile(r"[^A-Za-z0-9 _.\-]")


#: The settings folder's name under ``%APPDATA%`` (Windows) or ``~/.config`` (elsewhere).
CONFIG_DIR_NAME = "AKF"
#: Names earlier builds used for that folder, newest first: the brand with two letters
#: swapped, and the project's working title before it. The first one found is moved to
#: :data:`CONFIG_DIR_NAME` once, so an update keeps the player's profiles and settings.
LEGACY_CONFIG_DIR_NAMES: tuple[str, ...] = ("AFK", "fishbot")


def config_dir() -> Path:
    """``%APPDATA%/AKF``, overridable with ``AKF_CONFIG_DIR`` for tests."""
    override = os.environ.get("AKF_CONFIG_DIR")
    if override:
        return Path(override)
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / ".config"
    current = base / CONFIG_DIR_NAME
    if current.exists():
        return current
    return _adopt_legacy_dir(base, current)


def _adopt_legacy_dir(base: Path, current: Path) -> Path:
    """Move an older build's settings folder to ``current``; return the folder to use.

    When the move fails (the folder is in use, say), the old folder keeps being used rather
    than starting over from defaults.
    """
    for name in LEGACY_CONFIG_DIR_NAMES:
        old = base / name
        if not old.is_dir():
            continue
        try:
            old.rename(current)
        except OSError as exc:
            logger.warning(
                "could not move settings from %s to %s (%s); using %s", old, current, exc, old
            )
            return old
        logger.info("moved settings from %s to %s", old, current)
        return current
    return current


def profiles_dir() -> Path:
    return config_dir() / "profiles"


def profile_filename(name: str) -> str:
    """Map a profile name to a filesystem-safe file name."""
    cleaned = _SAFE_NAME.sub("_", name).strip() or "profile"
    return f"{cleaned}.json"


def list_profiles() -> list[str]:
    directory = profiles_dir()
    if not directory.is_dir():
        return []
    return sorted(path.stem for path in directory.glob("*.json"))


def load_profile(name: str) -> Profile:
    path = profiles_dir() / profile_filename(name)
    return Profile.model_validate_json(path.read_text(encoding="utf-8"))


def save_profile(profile: Profile) -> Path:
    directory = profiles_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / profile_filename(profile.name)
    _write_json(path, profile.model_dump(mode="json"))
    return path


def delete_profile(name: str) -> None:
    path = profiles_dir() / profile_filename(name)
    if not path.is_file():
        msg = f"no saved profile named {name!r}"
        raise FileNotFoundError(msg)
    path.unlink()


def load_settings() -> Settings:
    path = config_dir() / "settings.json"
    if not path.is_file():
        return Settings()
    return Settings.model_validate_json(path.read_text(encoding="utf-8"))


def save_settings(settings: Settings) -> Path:
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "settings.json"
    _write_json(path, settings.model_dump(mode="json"))
    return path


def _write_json(path: Path, payload: dict[str, object]) -> None:
    """Write via a temporary file so a crash never leaves a half-written profile."""
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(path)


def touch_calibration(profile: Profile) -> Profile:
    """Stamp the profile as calibrated right now."""
    return profile.model_copy(update={"calibrated_at": datetime.now(UTC)})
