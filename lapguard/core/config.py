"""Typed configuration stored as TOML in the per-user config directory.

The schema *is* the dataclasses below: defaults live on the fields, the loader
merges a (possibly partial) TOML file over them, type-checks every value,
warns about unknown keys and validates ranges. Missing keys always fall back
to defaults, so new options can be added without breaking old config files.

Paths (overridable with env vars, used by tests and --dev runs):
  config : %APPDATA%\\LapGuard\\config.toml   | ~/.config/lapguard/config.toml   (LAPGUARD_CONFIG_DIR)
  state  : %LOCALAPPDATA%\\LapGuard\\         | ~/.local/state/lapguard/         (LAPGUARD_STATE_DIR)
  DISARM : ~/.lapguard/DISARM                                                    (LAPGUARD_EMERGENCY_DIR)
"""

from __future__ import annotations

import dataclasses
import os
import sys
import tomllib
import typing
from dataclasses import dataclass, field
from pathlib import Path

import tomli_w

from .fsutil import atomic_write_text

APP_NAME = "LapGuard"


class ConfigError(ValueError):
    pass


# --------------------------------------------------------------------------- paths

def config_dir() -> Path:
    if env := os.environ.get("LAPGUARD_CONFIG_DIR"):
        return Path(env)
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_NAME
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "lapguard"


def state_dir() -> Path:
    if env := os.environ.get("LAPGUARD_STATE_DIR"):
        return Path(env)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "lapguard"


def emergency_file() -> Path:
    base = os.environ.get("LAPGUARD_EMERGENCY_DIR") or str(Path.home() / ".lapguard")
    return Path(base) / "DISARM"


def config_path() -> Path:
    return config_dir() / "config.toml"


def journal_path() -> Path:
    return state_dir() / "journal.json"


# --------------------------------------------------------------------------- schema

@dataclass
class GeneralConfig:
    owner_name: str = ""
    photo_path: str = ""
    hotkey: str = "Ctrl+Alt+Shift+L"
    countdown_s: float = 10.0
    grace_s: float = 8.0
    max_attempts: int = 2
    siren_file: str = ""          # empty = bundled siren
    emergency_file: bool = True   # watch ~/.lapguard/DISARM while armed (always on in --dev)
    log_level: str = "INFO"


@dataclass
class PasskeyConfig:
    hash: str = ""  # argon2id PHC string; never plaintext


@dataclass
class KeyboardSensor:
    enabled: bool = True
    immediate: bool = False


@dataclass
class MouseSensor:
    enabled: bool = True           # clicks on the armed overlay count as a touch
    immediate: bool = False
    movement: bool = False         # also trigger on pointer movement (off: cursor drift)
    move_threshold_px: int = 50


@dataclass
class PowerSensor:
    enabled: bool = True
    immediate: bool = False
    poll_s: float = 0.5


@dataclass
class ShutdownSensor:
    enabled: bool = True
    immediate: bool = True
    block: bool = True             # veto/delay shutdown & logoff where the OS allows


@dataclass
class LidSensor:
    enabled: bool = True
    immediate: bool = True
    override_power_actions: bool = True  # lid & power button -> "do nothing" while armed


@dataclass
class CameraSensor:
    enabled: bool = True
    immediate: bool = False
    device_index: int = 0
    width: int = 320
    height: int = 240
    fps: int = 5
    baseline_s: float = 2.0
    brightness_drop: float = 0.6       # fraction below baseline mean = covered / lid closed
    motion_pixel_delta: int = 25       # per-pixel change (0-255) that counts as "changed"
    motion_fraction: float = 0.35      # fraction of changed pixels that counts as movement
    motion_frames: int = 3             # consecutive frames required (debounce)


@dataclass
class DevicesSensor:
    enabled: bool = True
    immediate: bool = True
    on_arrival: bool = True
    on_removal: bool = True
    ignore: list[str] = field(default_factory=list)  # device-ID substrings to ignore


@dataclass
class SessionSensor:
    enabled: bool = True
    immediate: bool = True  # session lock (Win+L) and resume from sleep


@dataclass
class SensorsConfig:
    keyboard: KeyboardSensor = field(default_factory=KeyboardSensor)
    mouse: MouseSensor = field(default_factory=MouseSensor)
    power: PowerSensor = field(default_factory=PowerSensor)
    shutdown: ShutdownSensor = field(default_factory=ShutdownSensor)
    lid: LidSensor = field(default_factory=LidSensor)
    camera: CameraSensor = field(default_factory=CameraSensor)
    devices: DevicesSensor = field(default_factory=DevicesSensor)
    session: SessionSensor = field(default_factory=SessionSensor)


@dataclass
class SirenAction:
    enabled: bool = True
    force_volume: bool = True
    enforce_interval_s: float = 1.0
    block_media_keys: bool = True


@dataclass
class LockscreenAction:
    enabled: bool = True
    armed_overlay: bool = True


@dataclass
class LogAction:
    enabled: bool = True


@dataclass
class ActionsConfig:
    siren: SirenAction = field(default_factory=SirenAction)
    lockscreen: LockscreenAction = field(default_factory=LockscreenAction)
    log: LogAction = field(default_factory=LogAction)


@dataclass
class WindowsConfig:
    disable_task_manager: bool = False      # HKCU policy while armed (never in --dev)
    disable_lock_workstation: bool = False  # HKCU policy while armed; default: Win+L triggers instead
    runonce_restore: bool = True            # HKCU RunOnce "lapguard --restore" while armed


@dataclass
class NtfyConfig:  # Phase 2 - parsed and validated now, unused until then
    enabled: bool = False
    url: str = ""
    topic: str = ""
    command_topic: str = ""
    token: str = ""
    priority: int = 5
    attach_snapshot: bool = True
    auth: str = "hmac"         # "hmac" | "totp"
    secret: str = ""
    max_clock_skew_s: int = 60


@dataclass
class Config:
    general: GeneralConfig = field(default_factory=GeneralConfig)
    passkey: PasskeyConfig = field(default_factory=PasskeyConfig)
    sensors: SensorsConfig = field(default_factory=SensorsConfig)
    actions: ActionsConfig = field(default_factory=ActionsConfig)
    windows: WindowsConfig = field(default_factory=WindowsConfig)
    ntfy: NtfyConfig = field(default_factory=NtfyConfig)

    # ---- derived helpers
    @property
    def has_passkey(self) -> bool:
        return bool(self.passkey.hash)

    def sensor_names(self) -> list[str]:
        return [f.name for f in dataclasses.fields(SensorsConfig)]

    def immediate_sensors(self) -> frozenset[str]:
        return frozenset(n for n in self.sensor_names() if getattr(self.sensors, n).immediate)

    def disabled_sensors(self) -> frozenset[str]:
        return frozenset(n for n in self.sensor_names() if not getattr(self.sensors, n).enabled)


# --------------------------------------------------------------------------- load / save

_HEADER = (
    "# LapGuard configuration. Missing keys fall back to defaults.\n"
    "# passkey.hash is an argon2id hash - set it with `lapguard --set-passkey`, never by hand.\n\n"
)


def load_config(path: Path | None = None) -> tuple[Config, list[str]]:
    """Return (config, warnings). A missing file yields defaults (first run)."""
    path = Path(path) if path else config_path()
    if not path.exists():
        return Config(), []
    try:
        # utf-8-sig: tolerate the BOM that PowerShell 5.1 / some Windows editors write
        data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: invalid TOML: {e}") from e
    warnings: list[str] = []
    cfg = _build(Config, data, "", warnings)
    validate(cfg)
    return cfg, warnings


def save_config(cfg: Config, path: Path | None = None) -> Path:
    validate(cfg)
    path = Path(path) if path else config_path()
    atomic_write_text(path, _HEADER + tomli_w.dumps(dataclasses.asdict(cfg)), private=True)
    return path


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _build(cls: type, data: object, path: str, warnings: list[str]):
    if not isinstance(data, dict):
        raise ConfigError(f"[{path or 'root'}] must be a table")
    hints = typing.get_type_hints(cls)
    names = {f.name for f in dataclasses.fields(cls)}
    for key in data:
        if key not in names:
            warnings.append(f"unknown config key '{_join(path, key)}' ignored")
    kwargs = {
        f.name: _coerce(hints[f.name], data[f.name], _join(path, f.name), warnings)
        for f in dataclasses.fields(cls)
        if f.name in data
    }
    return cls(**kwargs)


def _coerce(tp: object, value: object, path: str, warnings: list[str]) -> object:
    if dataclasses.is_dataclass(tp):
        return _build(tp, value, path, warnings)  # type: ignore[arg-type]
    if typing.get_origin(tp) is list:
        (item_tp,) = typing.get_args(tp)
        if not isinstance(value, list):
            raise ConfigError(f"{path}: expected a list, got {type(value).__name__}")
        return [_coerce(item_tp, v, f"{path}[{i}]", warnings) for i, v in enumerate(value)]
    if tp is bool:
        ok = isinstance(value, bool)
    elif tp is int:
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif tp is float:
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        if ok:
            value = float(value)  # type: ignore[arg-type]
    elif tp is str:
        ok = isinstance(value, str)
    else:  # pragma: no cover - schema uses only the types above
        raise ConfigError(f"{path}: unsupported schema type {tp!r}")
    if not ok:
        raise ConfigError(f"{path}: expected {getattr(tp, '__name__', tp)}, got {type(value).__name__} ({value!r})")
    return value


def _check(cond: bool, msg: str) -> None:
    if not cond:
        raise ConfigError(msg)


def validate(cfg: Config) -> None:
    g = cfg.general
    _check(0 <= g.countdown_s <= 300, "general.countdown_s must be between 0 and 300")
    _check(1 <= g.grace_s <= 120, "general.grace_s must be between 1 and 120")
    _check(1 <= g.max_attempts <= 20, "general.max_attempts must be between 1 and 20")
    _check(bool(g.hotkey.strip()), "general.hotkey must not be empty")
    _check(g.log_level.upper() in {"DEBUG", "INFO", "WARNING", "ERROR"},
           "general.log_level must be DEBUG, INFO, WARNING or ERROR")
    _check(cfg.passkey.hash == "" or cfg.passkey.hash.startswith("$argon2id$"),
           "passkey.hash must be an argon2id hash - use `lapguard --set-passkey`")

    s = cfg.sensors
    _check(s.mouse.move_threshold_px >= 1, "sensors.mouse.move_threshold_px must be >= 1")
    _check(0.1 <= s.power.poll_s <= 10, "sensors.power.poll_s must be between 0.1 and 10")
    c = s.camera
    _check(c.device_index >= 0, "sensors.camera.device_index must be >= 0")
    _check(c.width > 0 and c.height > 0, "sensors.camera.width/height must be > 0")
    _check(1 <= c.fps <= 30, "sensors.camera.fps must be between 1 and 30")
    _check(0 < c.baseline_s <= 30, "sensors.camera.baseline_s must be in (0, 30]")
    _check(0 < c.brightness_drop < 1, "sensors.camera.brightness_drop must be in (0, 1)")
    _check(0 < c.motion_pixel_delta <= 255, "sensors.camera.motion_pixel_delta must be in (0, 255]")
    _check(0 < c.motion_fraction <= 1, "sensors.camera.motion_fraction must be in (0, 1]")
    _check(c.motion_frames >= 1, "sensors.camera.motion_frames must be >= 1")

    _check(0.2 <= cfg.actions.siren.enforce_interval_s <= 10,
           "actions.siren.enforce_interval_s must be between 0.2 and 10")

    n = cfg.ntfy
    _check(1 <= n.priority <= 5, "ntfy.priority must be between 1 and 5")
    _check(n.auth in {"hmac", "totp"}, "ntfy.auth must be 'hmac' or 'totp'")
    _check(n.max_clock_skew_s >= 1, "ntfy.max_clock_skew_s must be >= 1")
