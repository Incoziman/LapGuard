import json

import pytest

from lapguard.core.journal import Journal


@pytest.fixture
def path(tmp_path):
    return tmp_path / "state" / "journal.json"


class FakeSystem:
    """Pretend system settings with a restorer that writes back into the dict."""

    def __init__(self, **values):
        self.values = dict(values)
        self.restore_order: list[str] = []
        self.fail_keys: set[str] = set()

    def restorer(self, key, original):
        if key in self.fail_keys:
            raise OSError(f"cannot restore {key}")
        self.values[key] = original
        self.restore_order.append(key)


def change(journal, system, key, new, kind="setting"):
    journal.record(kind, key, system.values[key])  # original first...
    system.values[key] = new                       # ...then the change


def test_no_file_when_nothing_pending(path):
    j = Journal(path)
    assert not j.has_pending()
    assert not path.exists()


def test_record_restore_lifo(path):
    sys_ = FakeSystem(lid=1, button=1, volume=30)
    j = Journal(path)
    j.register("setting", sys_.restorer)
    change(j, sys_, "lid", 0)
    change(j, sys_, "button", 0)
    change(j, sys_, "volume", 100)
    assert path.exists() and len(j) == 3

    report = j.restore_all()
    assert report.ok
    assert sys_.values == {"lid": 1, "button": 1, "volume": 30}
    assert sys_.restore_order == ["volume", "button", "lid"]
    assert not path.exists()


def test_crash_then_restore_on_next_start(path):
    sys_ = FakeSystem(lid=1)
    first_run = Journal(path)
    change(first_run, sys_, "lid", 0)
    del first_run  # process "crashes" without restoring

    second_run = Journal(path)
    second_run.register("setting", sys_.restorer)
    assert second_run.has_pending()
    assert second_run.restore_all().ok
    assert sys_.values["lid"] == 1


def test_first_original_wins(path):
    """Crash, then re-arm without restoring: must not remember LapGuard's own value."""
    sys_ = FakeSystem(lid=1)
    j = Journal(path)
    change(j, sys_, "lid", 0)
    assert j.record("setting", "lid", sys_.values["lid"]) is False  # now 0 - ignored
    j.register("setting", sys_.restorer)
    j.restore_all()
    assert sys_.values["lid"] == 1


def test_failure_keeps_entry_and_continues(path):
    sys_ = FakeSystem(a=1, b=2, c=3)
    j = Journal(path)
    j.register("setting", sys_.restorer)
    for k in "abc":
        change(j, sys_, k, 0)
    sys_.fail_keys = {"b"}
    report = j.restore_all()
    assert not report.ok
    assert report.restored == ["setting:c", "setting:a"]
    assert report.failed[0][0] == "setting:b"
    assert [e.key for e in Journal(path).entries()] == ["b"]  # persisted

    sys_.fail_keys.clear()
    assert j.restore_all().ok
    assert sys_.values == {"a": 1, "b": 2, "c": 3}


def test_unknown_kind_is_kept(path):
    j = Journal(path)
    j.record("registry", "HKCU\\X", 0)
    report = j.restore_all()
    assert report.skipped == ["registry:HKCU\\X"]
    assert j.has_pending()


def test_discard(path):
    j = Journal(path)
    j.record("setting", "lid", 1)
    j.discard("setting", "lid")
    assert not j.has_pending() and not path.exists()


def test_rejects_unserialisable_before_touching_anything(path):
    j = Journal(path)
    with pytest.raises(TypeError):
        j.record("setting", "x", object())
    assert not path.exists()


@pytest.mark.parametrize("content", ["{not json", json.dumps({"version": 99, "entries": []}),
                                     json.dumps({"version": 1, "entries": [{"bogus": 1}]})])
def test_corrupt_journal_quarantined_not_deleted(path, content):
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    j = Journal(path)
    assert not j.has_pending()
    assert j.load_error and "moved to" in j.load_error
    quarantined = list(path.parent.glob("journal.corrupt-*.json"))
    assert len(quarantined) == 1 and quarantined[0].read_text(encoding="utf-8") == content
    report = j.restore_all()
    assert not report.ok and report.load_error


def test_complex_original_values(path):
    j = Journal(path)
    original = {"ac": 1, "dc": 1, "scheme": "381b4222-f694-41f0-9685-ff5bb260df2e"}
    j.record("powercfg", "LIDACTION", original)
    assert Journal(path).entries()[0].original == original
