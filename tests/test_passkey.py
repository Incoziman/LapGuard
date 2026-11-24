import pytest

from lapguard.core import config as cfgmod
from lapguard.core.passkey import (
    PasskeyError,
    hash_passkey,
    make_verifier,
    needs_rehash,
    validate_passkey,
    verify_passkey,
)


@pytest.fixture(scope="module")
def stored():
    return hash_passkey("correct horse 42")


def test_hash_is_argon2id_and_has_no_plaintext(stored):
    assert stored.startswith("$argon2id$")
    assert "correct horse" not in stored
    assert not needs_rehash(stored)


def test_verify_roundtrip(stored):
    assert verify_passkey(stored, "correct horse 42")
    assert not verify_passkey(stored, "correct horse 43")
    assert not verify_passkey(stored, "")


def test_salted(stored):
    assert hash_passkey("correct horse 42") != stored


def test_make_verifier(stored):
    v = make_verifier(stored)
    assert v("correct horse 42") and not v("nope")


@pytest.mark.parametrize("bad_hash", ["", "garbage", "$argon2i$v=19$m=65536,t=3,p=4$abc$def",
                                      "$argon2id$v=19$m=65536,t=3,p=4$broken"])
def test_bad_or_foreign_hashes_never_verify(bad_hash):
    assert not verify_passkey(bad_hash, "anything")


@pytest.mark.parametrize("pk,why", [
    ("abc", "at least"),
    ("x" * 129, "at most"),
    ("pässword", "ASCII"),
    ("tab\there", "ASCII"),
    (" leading", "space"),
    ("trailing ", "space"),
])
def test_validation_rejects(pk, why):
    with pytest.raises(PasskeyError, match=why):
        validate_passkey(pk)


def test_validation_accepts_symbols_and_inner_space():
    validate_passkey("a b!@#$%^&*()_+-=[]{};:'\",.<>/?\\|`~1")


def test_config_file_never_contains_plaintext(tmp_path):
    cfg = cfgmod.Config()
    cfg.passkey.hash = hash_passkey("hunter2-plain")
    path = cfgmod.save_config(cfg)
    text = path.read_text(encoding="utf-8")
    assert "hunter2-plain" not in text
    assert "$argon2id$" in text
    loaded, _ = cfgmod.load_config(path)
    assert verify_passkey(loaded.passkey.hash, "hunter2-plain")
