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
    type: Literal["test_cast"]


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
    | ListAudioDevices,
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
    | ErrorEvent
)
