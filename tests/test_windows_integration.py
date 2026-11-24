"""Real Windows API checks with no lasting side effects.

* registry: only a private test key under HKCU\\Software\\LapGuardTest
* power: read-only, plus a same-value write to prove we have permission
* message window: started and stopped; initial lid / power state read
No keyboard hook is installed here (that would swallow your typing).
"""

import time

import pytest

winreg = pytest.importorskip("winreg", reason="Windows only")

TEST_KEY = r"Software\LapGuardTest"


@pytest.fixture
def clean_test_key():
    yield
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, TEST_KEY)
    except FileNotFoundError:
        pass


def test_registry_journaled_set_and_restore(tmp_path, clean_test_key):
    from lapguard.core.journal import Journal
    from lapguard.platform.win32 import registry
    from lapguard.platform.windows import register_restorers

    j = Journal(tmp_path / "j.json")
    register_restorers(j)
    registry.set_value(TEST_KEY, "Existing", winreg.REG_DWORD, 7)
    registry.journaled_set(j, TEST_KEY, "Existing", winreg.REG_DWORD, 1)
    registry.journaled_set(j, TEST_KEY, "New", winreg.REG_SZ, "hello")
    assert registry.snapshot(TEST_KEY, "Existing")["value"] == 1
    assert registry.snapshot(TEST_KEY, "New")["value"] == "hello"

    assert j.restore_all().ok
    assert registry.snapshot(TEST_KEY, "Existing")["value"] == 7
    assert registry.snapshot(TEST_KEY, "New")["exists"] is False


def test_restore_command_points_at_lapguard():
    from lapguard.platform.win32.registry import restore_command

    cmd = restore_command()
    assert "--restore" in cmd and cmd.startswith('"')


def test_power_settings_readable_and_writable():
    from lapguard.platform.win32 import powersettings as ps

    scheme = ps.active_scheme()
    for setting in (ps.LIDACTION, ps.PBUTTONACTION):
        for ac in (True, False):
            value = ps.read_value(scheme, setting.subgroup, setting.guid, ac=ac)
            assert 0 <= value <= 4
            # same-value write: proves permission (no admin) without changing anything
            ps.write_value(scheme, setting.subgroup, setting.guid, value, ac=ac)
            assert ps.read_value(scheme, setting.subgroup, setting.guid, ac=ac) == value


def test_message_window_reports_initial_lid_and_power(tmp_path):
    from lapguard.core.config import Config
    from lapguard.core.journal import Journal
    from lapguard.platform.windows import WindowsAdapter

    lines = []
    adapter = WindowsAdapter(lambda e: None, Journal(tmp_path / "j.json"), Config(), dev=True, diag=lines.append)
    adapter.start()
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and "unknown" in adapter.status().values():
            time.sleep(0.05)
        status = adapter.status()
    finally:
        adapter.stop()
    assert status["lid"] in ("open", "closed")
    assert status["power"] in ("AC", "battery")
    assert any("initial state" in line for line in lines)
