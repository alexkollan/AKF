"""Fishing bot engine: capture, detection, state machine, input and the local service.

Everything that decides or acts lives here. The UI is a thin client that talks to
`engine.server` over a local WebSocket, so the bot keeps running with the UI closed.
"""

__version__ = "0.1.0"
