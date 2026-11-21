"""Where the FishBridge addon goes, from the game's executable path."""

from pathlib import Path

from tools.install_addon import game_folder


def test_windows_exe_sits_in_the_game_folder():
    exe = Path("D:/Games/World of Warcraft/_forever_/Wow.exe")
    assert game_folder(exe) == Path("D:/Games/World of Warcraft/_forever_")


def test_mac_executable_sits_inside_the_app_bundle():
    exe = Path(
        "/Applications/World of Warcraft/_forever_/World of Warcraft.app/Contents/MacOS/"
        "World of Warcraft"
    )
    assert game_folder(exe) == Path("/Applications/World of Warcraft/_forever_")
