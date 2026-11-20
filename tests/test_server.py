"""The WebSocket service end to end: a real uvicorn server on a free port, a real client.

Sessions are simulated, hotkeys are off; everything else — the event loop, the pump, the
origin check, the JSON frames — is what the UI will talk to.
"""

import contextlib
import json
import random
import socket
import threading
import time
import urllib.request
from types import SimpleNamespace

import pytest
import uvicorn
from websockets.exceptions import InvalidStatus
from websockets.sync.client import connect

from engine.controller import Controller
from engine.hotkeys import SwallowingHotkeys
from engine.server import allowed_origins, create_app
from engine.session import Session

TIMEOUT = 5.0


def sessions(profile, **kwargs):
    return Session(profile, simulate=True, rng=random.Random(0), **kwargs)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def server():
    app = create_app(Controller(session_factory=sessions), enable_hotkeys=False)
    port = free_port()
    instance = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    )
    thread = threading.Thread(target=instance.run, daemon=True)
    thread.start()
    deadline = time.perf_counter() + TIMEOUT
    while not instance.started:
        if time.perf_counter() > deadline:
            pytest.fail("server did not start")
        time.sleep(0.01)
    yield f"127.0.0.1:{port}"
    instance.should_exit = True
    thread.join(TIMEOUT)


def recv(ws) -> dict:
    return json.loads(ws.recv(timeout=TIMEOUT))


def recv_until(ws, predicate, limit=200) -> dict:
    for _ in range(limit):
        frame = recv(ws)
        if predicate(frame):
            return frame
    msg = "expected frame never arrived"
    raise AssertionError(msg)


@contextlib.contextmanager
def open_socket(server: str, **kwargs):
    """Connect, check the snapshot every new client gets, hand over the socket."""
    with connect(f"ws://{server}/ws", open_timeout=TIMEOUT, **kwargs) as ws:
        snapshot = [recv(ws)["type"] for _ in range(4)]
        assert snapshot == ["config", "profiles", "state", "stats"]
        yield ws


def send(ws, frame: dict) -> None:
    ws.send(json.dumps(frame))


def test_health(server):
    with urllib.request.urlopen(f"http://{server}/health", timeout=TIMEOUT) as response:
        body = json.loads(response.read())
    assert body["ok"] is True
    assert body["status"] == "stopped"


def test_start_and_stop_over_the_socket(server):
    with open_socket(server) as ws:
        send(ws, {"type": "start"})
        recv_until(ws, lambda f: f["type"] == "state" and f["status"] == "running")
        send(ws, {"type": "stop"})
        frame = recv_until(ws, lambda f: f["type"] == "state" and f["status"] == "stopped")
    assert frame["reason"] == "stopped by user"


def test_running_session_streams_logs_and_stats(server):
    with open_socket(server) as ws:
        send(ws, {"type": "start"})
        log = recv_until(ws, lambda f: f["type"] == "log" and "cast #1" in f["line"]["message"])
        assert log["line"]["t"] > 1.6e12  # epoch milliseconds
        stats = recv_until(ws, lambda f: f["type"] == "stats" and f["stats"]["casts"] >= 1)
        assert stats["stats"]["startedAt"] is not None
        send(ws, {"type": "emergency_stop"})
        frame = recv_until(ws, lambda f: f["type"] == "state" and f["status"] == "stopped")
    assert frame["reason"] == "emergency stop"


def test_bad_frames_answer_with_an_error_not_a_disconnect(server):
    with open_socket(server) as ws:
        send(ws, {"type": "launch_missiles"})
        assert recv_until(ws, lambda f: f["type"] == "error")["command"] == "launch_missiles"

        send(ws, {"type": "set_config", "patch": {"timing": {"land_delay_ms": -1}}})
        assert "land_delay_ms" in recv_until(ws, lambda f: f["type"] == "error")["message"]

        send(ws, {"type": "load_profile", "name": "nope"})
        assert recv_until(ws, lambda f: f["type"] == "error")["command"] == "load_profile"

        send(ws, {"type": "list_profiles"})  # still alive after three errors
        recv_until(ws, lambda f: f["type"] == "profiles")


def test_config_changes_are_merged_and_broadcast_to_every_client(server):
    with open_socket(server) as first, open_socket(server) as second:
        send(first, {"type": "set_config", "patch": {"keys": {"fish_key": "["}}})
        frame = recv_until(second, lambda f: f["type"] == "config")
    assert frame["profile"]["keys"]["fish_key"] == "["
    assert frame["profile"]["keys"]["cast_key"] == "9"  # merged, not replaced


def test_profiles_round_trip_over_the_socket(server):
    with open_socket(server) as ws:
        send(ws, {"type": "set_config", "patch": {"notes": "north shore"}})
        send(ws, {"type": "save_profile", "name": "shore"})
        frame = recv_until(ws, lambda f: f["type"] == "profiles" and "shore" in f["names"])
        assert frame["active"] == "shore"
        send(ws, {"type": "load_profile", "name": "shore"})
        config = recv_until(ws, lambda f: f["type"] == "config" and f["profile"]["name"] == "shore")
    assert config["profile"]["notes"] == "north shore"


def test_a_web_page_from_another_origin_is_refused(server):
    with (
        pytest.raises(InvalidStatus),
        connect(f"ws://{server}/ws", origin="http://evil.example", open_timeout=TIMEOUT),
    ):
        pass


def test_the_ui_origins_are_accepted(server):
    for origin in sorted(allowed_origins(8777)):
        with open_socket(server, origin=origin):
            pass


def test_hotkeys_call_the_controller_and_are_swallowed(monkeypatch):
    calls = []
    hotkeys = SwallowingHotkeys({"<f9>": lambda: calls.append("f9")})

    class Swallowed(Exception):
        pass

    def suppress():
        raise Swallowed

    monkeypatch.setattr(hotkeys._listener, "suppress_event", suppress)

    with pytest.raises(Swallowed):
        hotkeys._filter(0x0100, SimpleNamespace(vkCode=0x78))  # F9 down
    with pytest.raises(Swallowed):
        hotkeys._filter(0x0100, SimpleNamespace(vkCode=0x78))  # auto-repeat
    with pytest.raises(Swallowed):
        hotkeys._filter(0x0101, SimpleNamespace(vkCode=0x78))  # F9 up
    assert calls == ["f9"]
    assert hotkeys._filter(0x0100, SimpleNamespace(vkCode=0x42)) is True  # B passes through
