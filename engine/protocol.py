"""The WebSocket wire format, as pydantic models.

This is the Python half of ``ui/src/lib/protocol.ts``; the two must change together.
Every frame is one JSON object with a ``type``. Field names are camelCase on the wire
(``alias_generator``), snake_case in Python. The only exception is the ``profile`` and
``settings`` payloads, which are the config models dumped as they are, so the UI sees the
same keys a profile JSON file has.

Commands go UI -> engine, events engine -> UI. Global hotkeys reach the same
:class:`~engine.controller.Controller` methods the commands do.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from pydantic.alias_generators import to_camel

from engine.stats import StatsSnapshot


class Wire(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    def to_json(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)


# --------------------------------------------------------------------------------------
# commands (UI -> engine)
# --------------------------------------------------------------------------------------


class Start(Wire):
    type: Literal["start"]


class Pause(Wire):
    type: Literal["pause"]


class Resume(Wire):
    type: Literal["resume"]


class Stop(Wire):
    type: Literal["stop"]


class EmergencyStop(Wire):
    type: Literal["emergency_stop"]


class ApplyLure(Wire):
    type: Literal["apply_lure"]
    name: str | None = None


class TestCast(Wire):
    """A test run: ``count`` casts with real outcomes, then stop."""

    type: Literal["test_cast"]
    count: Annotated[int, Field(ge=1, le=50)] = 1


class SetConfig(Wire):
    """Deep-merge ``patch`` into the active profile. Applies from the next cast."""

    type: Literal["set_config"]
    patch: dict[str, Any]


class SetSettings(Wire):
    """Deep-merge ``patch`` into the machine-wide settings and save them."""

    type: Literal["set_settings"]
    patch: dict[str, Any]


class LoadProfile(Wire):
    type: Literal["load_profile"]
    name: str


class SaveProfile(Wire):
    """Save the active profile; with ``name``, save it under that name (save as)."""

    type: Literal["save_profile"]
    name: str | None = None


class ListProfiles(Wire):
    type: Literal["list_profiles"]


class ScreenshotRoi(Wire):
    type: Literal["screenshot_roi"]


class ScreenshotClient(Wire):
    """The whole WoW client, downscaled: the canvas for the ROI editor and the eyedropper."""

    type: Literal["screenshot_client"]


class ImportProfile(Wire):
    """Replace the active profile with ``profile`` (a full profile JSON) and save it."""

    type: Literal["import_profile"]
    profile: dict[str, Any]


class DeleteProfile(Wire):
    type: Literal["delete_profile"]
    name: str


class ListAudioDevices(Wire):
    type: Literal["list_audio_devices"]


class ListWindows(Wire):
    type: Literal["list_windows"]


class SelectWindow(Wire):
    """Calibration step 1: this is the game window."""

    type: Literal["select_window"]
    hwnd: int


class CaptureBobber(Wire):
    """Calibration step 3: the bobber is here, as fractions of the client area."""

    type: Literal["capture_bobber"]
    x: Annotated[float, Field(ge=0.0, le=1.0)]
    y: Annotated[float, Field(ge=0.0, le=1.0)]


class CalibrateSound(Wire):
    """Calibration step 4: listen to the spot's ambience for this long."""

    type: Literal["calibrate_sound"]
    seconds: Annotated[float, Field(ge=5.0, le=120.0)] = 20.0


class MeasureTiming(Wire):
    """Calibration step 5: cast this many times and time the landing."""

    type: Literal["measure_timing"]
    casts: Annotated[int, Field(ge=1, le=10)] = 3


class CancelCalibration(Wire):
    type: Literal["cancel_calibration"]


class FinishCalibration(Wire):
    """Stamp the profile as calibrated now and save it."""

    type: Literal["finish_calibration"]


Command = Annotated[
    Start
    | Pause
    | Resume
    | Stop
    | EmergencyStop
    | ApplyLure
    | TestCast
    | SetConfig
    | SetSettings
    | LoadProfile
    | SaveProfile
    | ListProfiles
    | ScreenshotRoi
    | ScreenshotClient
    | ImportProfile
    | DeleteProfile
    | ListAudioDevices
    | ListWindows
    | SelectWindow
    | CaptureBobber
    | CalibrateSound
    | MeasureTiming
    | CancelCalibration
    | FinishCalibration,
    Field(discriminator="type"),
]

COMMAND = TypeAdapter(Command)


def parse_command(payload: object) -> Command:
    """Validate one incoming frame; raises ``pydantic.ValidationError`` when malformed."""
    return COMMAND.validate_python(payload)


# --------------------------------------------------------------------------------------
# events (engine -> UI)
# --------------------------------------------------------------------------------------


class StateEvent(Wire):
    type: Literal["state"] = "state"
    status: str
    state: str
    reason: str | None = None


class StatsEvent(Wire):
    type: Literal["stats"] = "stats"
    stats: StatsSnapshot
    #: Seconds left on the active lure; ``None`` when no lure is on.
    lure_remaining_s: float | None = None


class Levels(Wire):
    """One detector reading. Pixel fields stay ``None`` until pixel detection exists."""

    t: float  # epoch milliseconds
    audio_level: float
    audio_floor: float
    audio_threshold: float
    armed: bool
    pixel_signal: float | None = None
    pixel_threshold: float | None = None


class LevelsEvent(Wire):
    type: Literal["levels"] = "levels"
    levels: Levels


class LogLine(Wire):
    t: float  # epoch milliseconds
    level: Literal["debug", "info", "warn", "error"] = "info"
    message: str


class LogEvent(Wire):
    type: Literal["log"] = "log"
    line: LogLine


class CatchWire(Wire):
    t: float  # epoch milliseconds
    wait_ms: int
    reaction_ms: int


class CatchEvent(Wire):
    type: Literal["catch"] = "catch"
    record: CatchWire


class ConfigEvent(Wire):
    type: Literal["config"] = "config"
    profile: dict[str, Any]
    settings: dict[str, Any]
    #: Factory values, so every screen can offer "Reset to defaults" without a copy of them.
    defaults: dict[str, Any]


class AudioDevice(Wire):
    index: int
    name: str
    channels: int
    sample_rate: int
    is_default: bool


class AudioDevicesEvent(Wire):
    type: Literal["audio_devices"] = "audio_devices"
    devices: list[AudioDevice]


class ClientShotEvent(Wire):
    """The WoW client area as a PNG, at most ``CLIENT_SHOT_MAX_WIDTH`` wide."""

    type: Literal["client_shot"] = "client_shot"
    png_base64: str
    width: int
    height: int
    client_width: int
    client_height: int


class ProfilesEvent(Wire):
    type: Literal["profiles"] = "profiles"
    names: list[str]
    active: str


class RoiEvent(Wire):
    type: Literal["roi"] = "roi"
    png_base64: str
    width: int
    height: int
    box: tuple[int, int, int, int] | None = None


class BridgeEvent(Wire):
    """What the FishBridge addon reports, once a second during a session."""

    type: Literal["bridge"] = "bridge"
    present: bool
    free_slots: int | None = None
    total_slots: int | None = None
    in_combat: bool | None = None
    pole_equipped: bool | None = None
    lure_active: bool | None = None
    lure_remaining_s: float | None = None
    channelling: bool | None = None
    channel_s: int | None = None


class WindowItem(Wire):
    hwnd: int
    title: str
    process: str
    width: int
    height: int
    monitor: int
    #: Runs one of the known WoW executables.
    likely_game: bool


class WindowsEvent(Wire):
    type: Literal["windows"] = "windows"
    windows: list[WindowItem]


class CalibrationEvent(Wire):
    """Progress or outcome of one calibration step."""

    type: Literal["calibration"] = "calibration"
    step: str
    status: Literal["running", "done", "error"]
    progress: float = 0.0
    message: str = ""
    result: dict[str, Any] = Field(default_factory=dict)


class ErrorEvent(Wire):
    type: Literal["error"] = "error"
    message: str
    command: str | None = None


Event = (
    StateEvent
    | StatsEvent
    | LevelsEvent
    | LogEvent
    | CatchEvent
    | ConfigEvent
    | ProfilesEvent
    | RoiEvent
    | AudioDevicesEvent
    | ClientShotEvent
    | BridgeEvent
    | WindowsEvent
    | CalibrationEvent
    | ErrorEvent
)
