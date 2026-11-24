import os
import sys

import pytest

from lapguard.core import config as cfgmod
from lapguard.core.config import Config, ConfigError, load_config, save_config


def write(text: str):
    path = cfgmod.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_missing_file_gives_defaults():
    cfg, warnings = load_config()
    assert cfg == Config()
    assert warnings == []
    assert not cfg.has_passkey
    assert cfg.general.hotkey == "Ctrl+Alt+Shift+L"
    assert cfg.general.countdown_s == 10 and cfg.general.grace_s == 8 and cfg.general.max_attempts == 2
    assert cfg.sensors.mouse.movement is False
    assert (cfg.sensors.camera.width, cfg.sensors.camera.height, cfg.sensors.camera.fps) == (320, 240, 5)
    assert cfg.ntfy.enabled is False


def test_roundtrip():
    cfg = Config()
    cfg.general.owner_name = "Keanu"
    cfg.sensors.devices.ignore = ["USB\\VID_1234"]
    cfg.sensors.camera.motion_fraction = 0.5
    path = save_config(cfg)
    loaded, warnings = load_config(path)
    assert loaded == cfg and warnings == []


def test_partial_file_merges_over_defaults():
    write("[general]\ngrace_s = 15\n\n[sensors.camera]\nenabled = false\n")
    cfg, _ = load_config()
    assert cfg.general.grace_s == 15.0
    assert cfg.general.countdown_s == 10.0
    assert cfg.sensors.camera.enabled is False
    assert cfg.sensors.camera.fps == 5
    assert cfg.disabled_sensors() == frozenset({"camera"})


def test_int_accepted_for_float():
    write("[general]\ncountdown_s = 5\n")
    cfg, _ = load_config()
    assert isinstance(cfg.general.countdown_s, float)


@pytest.mark.parametrize("text,match", [
    ('[general]\nmax_attempts = "two"\n', "general.max_attempts"),
    ("[general]\nmax_attempts = 2.5\n", "general.max_attempts"),
    ("[sensors.keyboard]\nenabled = 1\n", "sensors.keyboard.enabled"),
    ('[sensors.devices]\nignore = "abc"\n', "sensors.devices.ignore"),
    ("[sensors.devices]\nignore = [1]\n", r"sensors.devices.ignore\[0\]"),
    ('general = "x"\n', r"\[general\]"),
])
def test_wrong_types_rejected(text, match):
    write(text)
    with pytest.raises(ConfigError, match=match):
        load_config()


@pytest.mark.parametrize("text", [
    "[general]\ngrace_s = 0\n",
    "[general]\nmax_attempts = 0\n",
    "[sensors.camera]\nbrightness_drop = 1.5\n",
    "[sensors.camera]\nfps = 0\n",
    '[ntfy]\nauth = "password"\n',
    '[passkey]\nhash = "plaintext-oops"\n',
    '[general]\nlog_level = "LOUD"\n',
])
def test_out_of_range_rejected(text):
    write(text)
    with pytest.raises(ConfigError):
        load_config()


def test_utf8_bom_accepted():
    path = cfgmod.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xef\xbb\xbf[general]\ngrace_s = 12\n")
    cfg, _ = load_config()
    assert cfg.general.grace_s == 12


def test_invalid_toml_reported():
    write("[general\n")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_config()


def test_unknown_keys_warn_but_load():
    write("[general]\nfavourite_colour = \"red\"\n\n[future_section]\nx = 1\n")
    cfg, warnings = load_config()
    assert cfg.general == Config().general
    assert any("general.favourite_colour" in w for w in warnings)
    assert any("future_section" in w for w in warnings)


def test_save_is_atomic_and_leaves_no_temp_files():
    path = save_config(Config())
    save_config(Config())
    assert [p.name for p in path.parent.iterdir()] == ["config.toml"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_config_is_private_on_posix():
    path = save_config(Config())
    assert (os.stat(path).st_mode & 0o777) == 0o600


def test_paths_follow_env_overrides(isolated_dirs):
    assert cfgmod.config_path() == isolated_dirs / "config" / "config.toml"
    assert cfgmod.journal_path() == isolated_dirs / "state" / "journal.json"
    assert cfgmod.emergency_file() == isolated_dirs / "emergency" / "DISARM"


def test_default_paths_per_os(monkeypatch):
    for var in ("LAPGUARD_CONFIG_DIR", "LAPGUARD_STATE_DIR", "LAPGUARD_EMERGENCY_DIR"):
        monkeypatch.delenv(var)
    if sys.platform == "win32":
        assert cfgmod.config_dir().parts[-2:] == ("Roaming", "LapGuard")
        assert cfgmod.state_dir().parts[-2:] == ("Local", "LapGuard")
    else:
        assert cfgmod.config_dir().name == "lapguard"
    assert cfgmod.emergency_file().parts[-2:] == (".lapguard", "DISARM")
