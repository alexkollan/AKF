"""The macOS backend's key map, keyboard hook and geometry, driven without pressing anything.

Like ``test_native_windows``, these pin the hook's two halves: a bound key fires once per
press *and* is swallowed when swallowing, and every other key passes through untouched.
Nothing here needs a macOS permission or the game.
"""

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS backend")

if sys.platform == "darwin":
    import Quartz

    from engine.capture import MonitorInfo, Rect
    from engine.hotkeys import GlobalHotkeys
    from engine.input import InputError, check_key
    from engine.native.macos import display
    from engine.native.macos.keyboard_mouse import keycode
    from tools.record_clip import MarkerListener

KVK_B, KVK_F7, KVK_F8, KVK_F9, KVK_F10, KVK_RIGHT_BRACKET = 0x0B, 0x62, 0x64, 0x65, 0x6D, 0x1E
EVENT = object()  # stands in for the CGEvent the tap would pass along


def test_letters_and_digits_map_by_position():
    assert keycode("a") == 0x00
    assert keycode("Z") == 0x06
    assert keycode("7") == 0x1A
    assert keycode("b") == KVK_B


def test_named_and_shifted_keys():
    assert keycode("]") == KVK_RIGHT_BRACKET
    assert keycode("}") == KVK_RIGHT_BRACKET  # shifted: its unshifted key
    assert keycode("f9") == keycode("F9") == KVK_F9
    assert keycode("shift") == keycode("lshift")
    assert keycode("esc") == keycode("escape")


@pytest.mark.parametrize("key", ["a", "1", "f8", "space", "numpad5", "up", "delete", "]"])
def test_every_common_key_has_a_code(key):
    assert keycode(key) >= 0
    assert check_key(key)


@pytest.mark.parametrize("key", ["insert", "f21", "f24"])
def test_keys_a_mac_does_not_have_are_errors(key):
    with pytest.raises(InputError, match="no"):
        check_key(key)


def test_unknown_key_is_an_error():
    with pytest.raises(InputError):
        keycode("wiggle")


def hook_of(hotkeys):
    return hotkeys._hook


def send(hook, code: int, event_type: int | None = None) -> bool:
    """Feed one key event (a key-down by default); returns True when it was swallowed."""
    event_type = Quartz.kCGEventKeyDown if event_type is None else event_type
    result = hook.handle(event_type, code, EVENT)
    assert result is None or result is EVENT
    return result is None


def test_hotkeys_fire_once_per_press_and_are_swallowed():
    calls = []
    hook = hook_of(GlobalHotkeys({"<f9>": lambda: calls.append("f9")}))
    assert send(hook, KVK_F9)
    assert send(hook, KVK_F9)  # auto-repeat
    assert send(hook, KVK_F9, Quartz.kCGEventKeyUp)
    assert calls == ["f9"]
    assert not send(hook, KVK_B)  # B passes through


def test_watching_without_swallowing_lets_the_key_through():
    calls = []
    hook = hook_of(GlobalHotkeys({"]": lambda: calls.append("]")}, swallow=False))
    for _ in range(4):  # auto-repeat while held
        assert not send(hook, KVK_RIGHT_BRACKET)
    assert not send(hook, KVK_RIGHT_BRACKET, Quartz.kCGEventKeyUp)
    assert not send(hook, KVK_RIGHT_BRACKET)
    assert calls == ["]", "]"]


def test_a_failing_callback_does_not_break_the_hook():
    def boom():
        raise RuntimeError("boom")

    hook = hook_of(GlobalHotkeys({"f10": boom}))
    assert send(hook, KVK_F10)
    assert send(hook, KVK_F10, Quartz.kCGEventKeyUp)


def test_recorder_keys_become_markers_and_are_swallowed():
    listener = MarkerListener(0.0)
    hook = hook_of(listener._hotkeys)
    for code in (KVK_F7, KVK_F8, KVK_F9):
        assert send(hook, code)
        assert send(hook, code, Quartz.kCGEventKeyUp)
    assert [kind for _t, kind, _label in listener.drain()] == ["cast", "landed", "bite"]
    assert not send(hook, KVK_B)
    assert not listener.stop_requested


RETINA = (
    None
    if sys.platform != "darwin"
    else MonitorInfo(
        index=0,
        device="Built-in Retina Display",
        handle=1,
        rect=Rect(0, 0, 1470, 956),
        work_rect=Rect(0, 33, 1470, 956),
        is_primary=True,
        scale=2.0,
    )
)


def test_a_window_covering_its_display_is_all_client():
    assert display._looks_titled(Rect(0, 0, 1470, 956), RETINA) is False
    window = Rect(0, 0, 1470, 956)
    assert display._client_rect(window, titled=False) == window


def test_a_titled_window_loses_its_title_bar():
    window = Rect(100, 100, 1124, 868)
    client = display._client_rect(window, titled=True)
    assert client.left == window.left and client.right == window.right
    assert client.bottom == window.bottom
    assert 20 <= client.top - window.top <= 40


def test_monitor_for_picks_the_one_holding_most_of_the_window():
    second = MonitorInfo(
        1, "second", 2, Rect(1470, 0, 3390, 1080), Rect(1470, 0, 3390, 1080), False
    )
    monitors = [RETINA, second]
    assert display.monitor_for(Rect(1400, 100, 2400, 900), monitors) is second
    assert display.monitor_for(Rect(-5000, -5000, -4000, -4000), monitors) is RETINA


def test_scale_converts_points_to_captured_pixels():
    assert RETINA.to_pixels(400) == 800


def test_own_process_path_is_readable():
    import os

    assert display.process_path(os.getpid()).endswith(("python", "python3", "python3.12"))
    assert display.process_path(999_999_999) == ""


def test_a_titled_window_in_another_space_counts_as_visible():
    """WoW in fullscreen while the player looks elsewhere is "off screen" to Quartz."""
    elsewhere = {Quartz.kCGWindowIsOnscreen: False, Quartz.kCGWindowName: "World of Warcraft"}
    helper = {Quartz.kCGWindowIsOnscreen: False, Quartz.kCGWindowName: ""}
    shown = {Quartz.kCGWindowIsOnscreen: True}
    assert display._counts_as_visible(elsewhere)
    assert not display._counts_as_visible(helper)
    assert display._counts_as_visible(shown)
