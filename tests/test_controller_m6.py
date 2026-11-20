"""What the controller gained for the UI: defaults, profile import/delete, notifications,
simulated levels for the chart, and serving the built UI."""

import json
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from pydantic import ValidationError

from engine import config as cfg
from engine.controller import Controller
from engine.protocol import StateEvent
from engine.session import Session


def sessions(profile, **kwargs):
    return Session(profile, simulate=True, rng=random.Random(0), **kwargs)


def test_config_event_carries_the_factory_defaults():
    controller = Controller(session_factory=sessions)
    controller.set_config({"timing": {"land_delay_ms": 3000}})
    config = next(e for e in controller.drain_events() if e.type == "config")
    assert config.profile["timing"]["land_delay_ms"] == 3000
    assert config.defaults["profile"]["timing"]["land_delay_ms"] == 2000
    assert config.defaults["settings"]["hotkeys"]["emergency_stop"] == "<f12>"


def test_import_replaces_the_active_profile_and_saves_it(isolated_config_dir):
    controller = Controller(session_factory=sessions)
    imported = cfg.Profile(name="from a friend", mode=cfg.Mode.INTERACT_KEY).model_dump(mode="json")
    controller.import_profile(imported)
    assert controller.profile.name == "from a friend"
    assert controller.profile.mode is cfg.Mode.INTERACT_KEY
    assert cfg.list_profiles() == ["from a friend"]


def test_a_malformed_import_changes_nothing(isolated_config_dir):
    controller = Controller(session_factory=sessions)
    before = controller.profile
    with pytest.raises(ValidationError):
        controller.import_profile({"name": "x", "mode": "Z"})
    assert controller.profile == before
    assert cfg.list_profiles() == []


def test_delete_profile(isolated_config_dir):
    controller = Controller(session_factory=sessions)
    controller.save_profile("lake")
    controller.save_profile("sea")
    controller.delete_profile("lake")
    assert cfg.list_profiles() == ["sea"]
    with pytest.raises(FileNotFoundError):
        controller.delete_profile("lake")


def test_lure_timer_is_absent_while_idle():
    assert Controller(session_factory=sessions).stats_event().lure_remaining_s is None


def test_simulated_sessions_feed_the_chart():
    """The UI's chart works with the game closed: simulated sessions emit levels."""
    seen = []
    session = Session(
        cfg.Profile(),
        simulate=True,
        rng=random.Random(0),
        on_levels=lambda level, t: seen.append(level),
    )
    thread = threading.Thread(target=session.run, kwargs={"duration_s": 0.6})
    thread.start()
    thread.join(5)
    assert len(seen) >= 3  # 10 Hz
    assert all(-90 < level.level_db < 0 for level in seen)


# --------------------------------------------------------------------------------------
# notifications
# --------------------------------------------------------------------------------------


@pytest.fixture
def webhook():
    """A local HTTP endpoint that records what was POSTed to it."""
    received: list[dict] = []
    arrived = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append({**json.loads(body), "user_agent": self.headers.get("User-Agent")})
            self.send_response(204)
            self.end_headers()
            arrived.set()

        def log_message(self, *args):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/hook", received, arrived
    server.shutdown()


def test_a_stop_by_itself_is_announced_on_the_webhook(webhook):
    url, received, arrived = webhook
    controller = Controller(session_factory=sessions)
    controller.settings = controller.settings.model_copy(
        update={"notifications": cfg.Notifications(webhook_url=url)}
    )
    controller._push_state(
        StateEvent(status="paused", state="RECOVER", reason="Stopped: 5 misses in a row")
    )
    assert arrived.wait(5)
    assert received[0]["reason"] == "Stopped: 5 misses in a row"
    assert "5 misses in a row" in received[0]["content"]
    assert received[0]["user_agent"] == "AFK"


def test_a_stop_by_the_user_is_not_announced(webhook):
    url, received, _arrived = webhook
    controller = Controller(session_factory=sessions)
    controller.settings = controller.settings.model_copy(
        update={"notifications": cfg.Notifications(webhook_url=url)}
    )
    controller._push_state(StateEvent(status="stopped", state="IDLE", reason="stopped by user"))
    time.sleep(0.3)
    assert received == []


def test_a_dead_webhook_does_not_break_anything():
    controller = Controller(session_factory=sessions)
    controller.settings = controller.settings.model_copy(
        update={"notifications": cfg.Notifications(webhook_url="http://127.0.0.1:9/nothing")}
    )
    controller._push_state(StateEvent(status="paused", state="IDLE", reason="WoW lost focus"))
    assert controller.status == "paused"


# --------------------------------------------------------------------------------------
# serving the UI
# --------------------------------------------------------------------------------------


def test_the_built_ui_is_served_next_to_the_api(tmp_path):
    import socket
    import urllib.request

    import uvicorn

    from engine.server import create_app

    (tmp_path / "index.html").write_text("<!doctype html><title>AFK</title>", encoding="utf-8")
    app = create_app(Controller(session_factory=sessions), enable_hotkeys=False, ui_dist=tmp_path)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.01)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as page:
            assert "<title>AFK</title>" in page.read().decode()
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as health:
            assert json.loads(health.read())["ok"] is True
    finally:
        server.should_exit = True
        thread.join(5)
