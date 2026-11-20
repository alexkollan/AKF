"""The engine as a local service: one WebSocket for the UI, global hotkeys for the keyboard.

    uv run python -m engine.server          # ws://127.0.0.1:8777/ws

Commands arrive as JSON frames (``engine.protocol``) and go to the
:class:`~engine.controller.Controller`; its events are broadcast to every connected client.
A client that connects late first gets a snapshot (config, profiles, state, stats), so the
UI can be closed and reopened while the bot keeps fishing.

The event loop never blocks: sessions run on their own thread, and the commands that touch
the disk or the screen run through ``asyncio.to_thread``.

**Who may connect.** The server listens on 127.0.0.1 only, but any web page open in a
browser on this machine could still try ``ws://127.0.0.1:8777``. Browsers always send an
``Origin`` header, so connections are accepted only from the UI's own origins (or with no
Origin at all — a local script, not a web page).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from typing import Any

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from engine import capture
from engine import config as cfg
from engine import protocol as proto
from engine.controller import Controller
from engine.hotkeys import SwallowingHotkeys

logger = logging.getLogger(__name__)

PUMP_INTERVAL_S = 0.05
STATS_INTERVAL_S = 1.0
UI_DEV_PORT = 5173
POLICY_VIOLATION = 1008


def allowed_origins(port: int) -> set[str]:
    hosts = ("localhost", "127.0.0.1")
    return {f"http://{host}:{p}" for host in hosts for p in (UI_DEV_PORT, port)}


class Hub:
    """The connected clients and the background pump that feeds them."""

    def __init__(self, controller: Controller) -> None:
        self.controller = controller
        self.clients: set[WebSocket] = set()

    async def broadcast(self, event: proto.Event) -> None:
        payload = event.to_json()
        for client in list(self.clients):
            try:
                await client.send_json(payload)
            except Exception:  # a dead socket must not stop the others
                self.clients.discard(client)

    async def pump(self) -> None:
        since_stats = 0.0
        while True:
            for event in self.controller.drain_events():
                await self.broadcast(event)
            since_stats += PUMP_INTERVAL_S
            if since_stats >= STATS_INTERVAL_S:
                since_stats = 0.0
                if self.controller.running:
                    await self.broadcast(self.controller.stats_event())
            await asyncio.sleep(PUMP_INTERVAL_S)


class HotkeyBinder:
    """Global hotkeys bound to the controller, rebound when the settings change."""

    def __init__(self, controller: Controller) -> None:
        self.controller = controller
        self._hotkeys: SwallowingHotkeys | None = None

    def bind(self, settings: cfg.Settings) -> None:
        self.unbind()
        keys = settings.hotkeys
        try:
            self._hotkeys = SwallowingHotkeys(
                {
                    keys.start_stop: self.controller.toggle_start_stop,
                    keys.pause: self.controller.toggle_pause,
                    keys.emergency_stop: self.controller.emergency_stop,
                }
            )
            self._hotkeys.start()
        except ValueError as exc:
            self._hotkeys = None
            self.controller.report_error(f"hotkeys not active: {exc}")

    def unbind(self) -> None:
        if self._hotkeys is not None:
            self._hotkeys.stop()
            self._hotkeys = None


async def handle(controller: Controller, command: proto.Command) -> None:
    """Run one validated command. Blocking work goes to a thread."""
    match command:
        case proto.Start():
            controller.start()
        case proto.Pause():
            controller.pause()
        case proto.Resume():
            controller.resume()
        case proto.Stop():
            controller.stop()
        case proto.EmergencyStop():
            controller.emergency_stop()
        case proto.ApplyLure():
            controller.apply_lure()
        case proto.TestCast():
            controller.test_cast()
        case proto.SetConfig(patch=patch):
            controller.set_config(patch)
        case proto.SetSettings(patch=patch):
            await asyncio.to_thread(controller.set_settings, patch)
        case proto.LoadProfile(name=name):
            await asyncio.to_thread(controller.load_profile, name)
        case proto.SaveProfile(name=name):
            await asyncio.to_thread(controller.save_profile, name)
        case proto.ListProfiles():
            await asyncio.to_thread(controller.list_profiles)
        case proto.ScreenshotRoi():
            await asyncio.to_thread(controller.screenshot_roi)


def create_app(
    controller: Controller | None = None,
    *,
    enable_hotkeys: bool = True,
) -> FastAPI:
    controller = controller or Controller()
    hub = Hub(controller)
    binder = HotkeyBinder(controller)
    origins = allowed_origins(controller.settings.server_port)

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        pump = asyncio.create_task(hub.pump())
        if enable_hotkeys:
            binder.bind(controller.settings)
            controller.on_settings_changed = binder.bind
        try:
            yield
        finally:
            binder.unbind()
            controller.emergency_stop()
            await asyncio.to_thread(controller.wait, 2.0)
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump

    app = FastAPI(title="fishbot engine", lifespan=lifespan)
    app.state.controller = controller
    app.state.hub = hub

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "status": controller.status, "clients": len(hub.clients)}

    @app.websocket("/ws")
    async def websocket(ws: WebSocket) -> None:
        origin = ws.headers.get("origin")
        if origin is not None and origin not in origins:
            logger.warning("refused WebSocket from origin %s", origin)
            await ws.close(code=POLICY_VIOLATION)
            return

        await ws.accept()
        for event in controller.snapshot():
            await ws.send_json(event.to_json())
        hub.clients.add(ws)
        try:
            while True:
                frame = await ws.receive_json()
                await _dispatch(controller, ws, frame)
        except WebSocketDisconnect:
            pass
        finally:
            hub.clients.discard(ws)

    return app


async def _dispatch(controller: Controller, ws: WebSocket, frame: object) -> None:
    name = frame.get("type") if isinstance(frame, dict) else None
    try:
        command = proto.parse_command(frame)
        await handle(controller, command)
    except ValidationError as exc:
        await ws.send_json(proto.ErrorEvent(message=_short(exc), command=name).to_json())
    except (FileNotFoundError, capture.CaptureError, OSError, ValueError) as exc:
        await ws.send_json(proto.ErrorEvent(message=str(exc), command=name).to_json())


def _short(exc: ValidationError) -> str:
    first = exc.errors()[0]
    where = ".".join(str(part) for part in first.get("loc", ()))
    return f"{where}: {first.get('msg', 'invalid')}" if where else str(first.get("msg"))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")
    settings = cfg.load_settings()
    uvicorn.run(
        create_app(),
        host=settings.server_host,
        port=settings.server_port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
