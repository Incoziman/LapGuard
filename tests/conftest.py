import pytest


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    """Never let a test touch the real config, journal or emergency file."""
    monkeypatch.setenv("LAPGUARD_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("LAPGUARD_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("LAPGUARD_EMERGENCY_DIR", str(tmp_path / "emergency"))
    return tmp_path


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()
