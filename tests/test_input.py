"""Key resolution and the dry-run controller. No key ever leaves this process."""

import pytest

from engine.input import InputController, InputError, MouseButton, scancode, virtual_key


def test_letters_and_digits_map_by_position():
    assert virtual_key("a") == 0x41
    assert virtual_key("Z") == 0x5A
    assert virtual_key("7") == 0x37


def test_named_keys():
    assert virtual_key("f8") == virtual_key("F8")
    assert virtual_key("shift") == virtual_key("lshift")
    assert virtual_key("esc") == virtual_key("escape")


def test_unknown_key_is_an_error():
    with pytest.raises(InputError):
        virtual_key("wiggle")
    with pytest.raises(InputError):
        virtual_key("  ")


@pytest.mark.parametrize("key", ["a", "1", "f8", "space", "numpad5", "up", "delete"])
def test_every_bindable_key_has_a_scancode(key):
    code, _extended = scancode(key)
    assert code > 0


@pytest.mark.parametrize("key", ["up", "down", "left", "right", "delete", "home"])
def test_navigation_keys_are_extended(key):
    _code, extended = scancode(key)
    assert extended, f"{key} needs the E0 prefix or the game reads the numpad twin"


def test_letters_are_not_extended():
    assert scancode("a")[1] is False


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
