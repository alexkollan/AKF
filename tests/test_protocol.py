"""The wire format: commands validate strictly, events serialise to the camelCase the UI reads."""

import pytest
from pydantic import ValidationError

from engine import protocol as proto
from engine.stats import SessionStats


@pytest.mark.parametrize(
    "frame",
    [
        {"type": "start"},
        {"type": "pause"},
        {"type": "resume"},
        {"type": "stop"},
        {"type": "emergency_stop"},
        {"type": "apply_lure"},
        {"type": "apply_lure", "name": "Bauble"},
        {"type": "test_cast"},
        {"type": "set_config", "patch": {"mode": "C"}},
        {"type": "set_settings", "patch": {"dry_run": True}},
        {"type": "load_profile", "name": "lake"},
        {"type": "save_profile"},
        {"type": "save_profile", "name": "lake"},
        {"type": "list_profiles"},
        {"type": "screenshot_roi"},
    ],
)
def test_every_command_in_protocol_ts_parses(frame):
    assert proto.parse_command(frame).type == frame["type"]


@pytest.mark.parametrize(
    "frame",
    [
        {"type": "launch_missiles"},
        {"type": "load_profile"},  # missing name
        {"type": "start", "extra": 1},  # unknown field
        {"no": "type"},
        "start",
    ],
)
def test_malformed_commands_are_rejected(frame):
    with pytest.raises(ValidationError):
        proto.parse_command(frame)


def test_events_use_camel_case_on_the_wire():
    stats = proto.StatsEvent(stats=SessionStats().snapshot(0.0)).to_json()
    assert set(stats["stats"]) >= {"elapsedMs", "consecutiveMisses", "catchesPerHour"}

    levels = proto.LevelsEvent(
        levels=proto.Levels(
            t=1.0, audio_level=-60, audio_floor=-63, audio_threshold=-49, armed=True
        )
    ).to_json()
    assert levels["levels"]["audioThreshold"] == -49
    assert levels["levels"]["pixelSignal"] is None

    roi = proto.RoiEvent(png_base64="x", width=2, height=3).to_json()
    assert roi == {"type": "roi", "pngBase64": "x", "width": 2, "height": 3, "box": None}


def test_state_event_carries_a_null_reason_by_default():
    assert proto.StateEvent(status="stopped", state="IDLE").to_json()["reason"] is None
