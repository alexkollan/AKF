import pytest
from pydantic import ValidationError

from engine import config as cfg


def test_defaults_are_valid():
    profile = cfg.Profile()
    assert profile.mode is cfg.Mode.SINGLE_BUTTON
    assert profile.detection.fusion is cfg.FusionMode.AUDIO
    assert 0.0 < profile.detection.search_roi.w <= 1.0


def test_config_directory_uses_akf_brand(monkeypatch, tmp_path):
    monkeypatch.delenv("AKF_CONFIG_DIR", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert cfg.config_dir() == tmp_path / "AKF"


@pytest.mark.parametrize("legacy", cfg.LEGACY_CONFIG_DIR_NAMES)
def test_an_older_settings_folder_is_moved_once(monkeypatch, tmp_path, legacy):
    monkeypatch.delenv("AKF_CONFIG_DIR", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    old = tmp_path / legacy
    (old / "profiles").mkdir(parents=True)
    (old / "profiles" / "spot.json").write_text("{}", encoding="utf-8")

    assert cfg.config_dir() == tmp_path / "AKF"
    assert (tmp_path / "AKF" / "profiles" / "spot.json").read_text(encoding="utf-8") == "{}"
    assert not old.exists()


def test_the_current_folder_wins_over_an_older_one(monkeypatch, tmp_path):
    monkeypatch.delenv("AKF_CONFIG_DIR", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    (tmp_path / "AKF").mkdir()
    (tmp_path / cfg.LEGACY_CONFIG_DIR_NAMES[0]).mkdir()

    assert cfg.config_dir() == tmp_path / "AKF"
    assert (tmp_path / cfg.LEGACY_CONFIG_DIR_NAMES[0]).exists()  # left alone


def test_a_failed_move_keeps_using_the_old_folder(monkeypatch, tmp_path):
    monkeypatch.delenv("AKF_CONFIG_DIR", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    old = tmp_path / cfg.LEGACY_CONFIG_DIR_NAMES[0]
    old.mkdir()

    def refuse(self, target):
        raise PermissionError("in use")

    monkeypatch.setattr(type(old), "rename", refuse)
    assert cfg.config_dir() == old


def test_unknown_key_is_rejected():
    with pytest.raises(ValidationError):
        cfg.Profile.model_validate({"name": "x", "mdoe": "A"})


def test_out_of_range_is_rejected():
    with pytest.raises(ValidationError):
        cfg.HsvRange(h_min=200)


@pytest.mark.parametrize(
    ("mode", "fusion", "expected"),
    [
        (cfg.Mode.SINGLE_BUTTON, cfg.FusionMode.AUDIO, False),
        (cfg.Mode.INTERACT_KEY, cfg.FusionMode.AUDIO, False),
        (cfg.Mode.RIGHT_CLICK, cfg.FusionMode.AUDIO, True),
        (cfg.Mode.SINGLE_BUTTON, cfg.FusionMode.AUDIO_AND_PIXEL, True),
    ],
)
def test_requires_bobber_vision(mode, fusion, expected):
    profile = cfg.Profile(mode=mode)
    profile.detection.fusion = fusion
    assert profile.requires_bobber_vision() is expected


def test_profile_round_trip(isolated_config_dir):
    profile = cfg.Profile(name="STV coast - night", mode=cfg.Mode.RIGHT_CLICK)
    profile.lures.lures.append(cfg.Lure(name="Aquadynamic Fish Attractor", key="6"))

    path = cfg.save_profile(profile)
    assert path.parent == isolated_config_dir / "profiles"

    loaded = cfg.load_profile(profile.name)
    assert loaded == profile
    assert cfg.list_profiles() == ["STV coast - night"]


def test_profile_filename_is_filesystem_safe():
    assert cfg.profile_filename("STV / coast: night?") == "STV _ coast_ night_.json"


def test_settings_round_trip():
    settings = cfg.Settings(active_profile="lake", server_port=9100)
    cfg.save_settings(settings)
    assert cfg.load_settings() == settings


def test_settings_default_when_missing():
    assert cfg.load_settings() == cfg.Settings()


def test_touch_calibration_stamps_time():
    profile = cfg.Profile()
    assert profile.calibrated_at is None
    assert cfg.touch_calibration(profile).calibrated_at is not None
