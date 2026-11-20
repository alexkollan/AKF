"""Key names and the dry-run controller. No key ever leaves this process.

The Windows scancode mapping is tested in ``test_native_windows.py``.
"""

import pytest

from engine.input import InputController, InputError, MouseButton, check_key
from engine.keys import normalize_key


def test_names_are_canonical():
    assert normalize_key("A") == "a"
    assert normalize_key(" F8 ") == "f8"
    assert normalize_key("shift") == "lshift"
    assert normalize_key("esc") == "escape"
    assert normalize_key("return") == "enter"
    assert normalize_key("]") == "]"


def test_unknown_key_is_an_error():
    with pytest.raises(InputError):
        normalize_key("wiggle")
    with pytest.raises(InputError):
        normalize_key("  ")
    with pytest.raises(InputError):
        check_key("wiggle")


def test_dry_run_rejects_an_unknown_key_before_recording_it():
    controller = InputController(dry_run=True)
    with pytest.raises(InputError):
        controller.tap("wiggle")
    assert controller.actions == []


def test_dry_run_records_instead_of_sending():
    controller = InputController(dry_run=True)
    controller.tap("8")
    kinds = [action.kind for action in controller.actions]
    assert kinds == ["key_down", "key_up"]
    assert controller.held == ((), ())


def test_dry_run_click_with_modifier():
    controller = InputController(dry_run=True)
    controller.click(MouseButton.RIGHT, modifiers=("shift",))
    assert [a.kind for a in controller.actions] == [
        "key_down",
        "button_down",
        "button_up",
        "key_up",
    ]
    assert controller.held == ((), ())


def test_release_all_drops_everything_held():
    controller = InputController(dry_run=True)
    controller.key_down("w")
    controller.button_down(MouseButton.LEFT)
    assert controller.held == (("w",), (MouseButton.LEFT,))

    controller.release_all()
    assert controller.held == ((), ())


def test_glide_emits_one_move_per_step():
    controller = InputController(dry_run=True)
    controller.glide_to(100, 100, steps=4, step_ms=0)
    assert [a.kind for a in controller.actions] == ["move"] * 4
    assert controller.actions[-1].target == "100,100"
