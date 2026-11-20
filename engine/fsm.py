"""Fishing state machine (IDLE, LURE, CAST, LOCATE, WATCH, HOOK, RECOVER) and modes A/B/C.

Implemented in M4. The FSM owns no I/O: it is driven by injected detectors, an injected
clock and an injected input backend, so it is unit-testable with fakes.
"""
