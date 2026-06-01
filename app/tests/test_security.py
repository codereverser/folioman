"""Secret / key management: Fernet resolution, PAN crypto, license keygen."""

from __future__ import annotations

import base64
import stat

import pytest
from cryptography.fernet import Fernet
from django.test import override_settings
from folioman_app.licensing.keys import generate_keypair, get_license_public_key_b64
from folioman_app.models import Investor
from folioman_app.security.checks import check_pan_encryption_key
from folioman_app.security.keys import (
    FernetKeyUnavailable,
    resolve_fernet_key,
)
from folioman_app.security.pan import decrypt_pan, encrypt_pan, normalize_pan, pan_hash

_PAN = "ABCDE1234F"


# --- PAN crypto (no DB) -----------------------------------------------------


def test_pan_encrypt_decrypt_roundtrip():
    token = encrypt_pan(_PAN)
    assert isinstance(token, bytes)
    assert token != _PAN.encode()  # actually encrypted
    assert decrypt_pan(token) == _PAN


def test_pan_normalize_and_hash_are_case_and_space_insensitive():
    assert normalize_pan("  abcde1234f ") == _PAN
    assert pan_hash("  abcde1234f ") == pan_hash(_PAN)


def test_pan_hash_distinguishes_different_pans():
    assert pan_hash(_PAN) != pan_hash("ZZZZZ9999Z")


def test_pan_hash_is_keyed_not_plain_sha256():
    import hashlib

    plain = hashlib.sha256(_PAN.encode()).hexdigest()
    assert pan_hash(_PAN) != plain  # HMAC-peppered, not a bare hash


# --- Investor PAN methods (DB) ----------------------------------------------


@pytest.mark.django_db
def test_investor_set_get_pan_roundtrip_and_lookup():
    from folioman_app.api.auth import get_local_user

    inv = Investor(name="Has PAN", owned_by=get_local_user())
    assert inv.has_pan is False
    inv.set_pan(_PAN)
    inv.save()
    assert inv.has_pan is True
    assert inv.get_pan() == _PAN
    # Findable by the keyed lookup hash without decrypting.
    assert Investor.objects.get(pan_hash=pan_hash(_PAN)).pk == inv.pk


@pytest.mark.django_db
def test_investor_set_pan_empty_clears():
    inv = Investor(name="No PAN")
    inv.set_pan(_PAN)
    inv.set_pan("")
    assert inv.pan_encrypted is None
    assert inv.pan_hash == ""
    assert inv.get_pan() is None


# --- Fernet key resolution --------------------------------------------------


def test_fernet_autogen_creates_0600_file_and_reuses(tmp_path, monkeypatch):
    monkeypatch.delenv("FOLIOMAN_FERNET_KEY", raising=False)
    key_file = tmp_path / "fernet.key"
    with override_settings(FERNET_KEY_PATH=str(key_file), FERNET_KEY_AUTOGEN=True):
        first = resolve_fernet_key()
        assert key_file.exists()
        Fernet(first)  # valid key
        # 0600 — owner read/write only.
        assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
        second = resolve_fernet_key()
        assert first == second  # reused, not regenerated


def test_fernet_env_overrides_everything(monkeypatch, tmp_path):
    env_key = Fernet.generate_key().decode()
    monkeypatch.setenv("FOLIOMAN_FERNET_KEY", env_key)
    with override_settings(FERNET_KEY_PATH=str(tmp_path / "unused.key"), FERNET_KEY_AUTOGEN=True):
        assert resolve_fernet_key() == env_key.encode()
        assert not (tmp_path / "unused.key").exists()  # env wins, no file made


def test_fernet_missing_raises(monkeypatch):
    monkeypatch.delenv("FOLIOMAN_FERNET_KEY", raising=False)
    with (
        override_settings(FERNET_KEY_PATH=None, FERNET_KEY_AUTOGEN=False, DEV_FERNET_KEY=None),
        pytest.raises(FernetKeyUnavailable),
    ):
        resolve_fernet_key()


# --- Startup guard check ----------------------------------------------------


def test_check_passes_when_key_not_required():
    # Default (base) settings: not required + dev fallback present.
    assert check_pan_encryption_key(None) == []


def test_check_errors_when_required_and_missing(monkeypatch):
    monkeypatch.delenv("FOLIOMAN_FERNET_KEY", raising=False)
    with override_settings(
        FERNET_KEY_REQUIRED=True,
        FERNET_KEY_PATH=None,
        FERNET_KEY_AUTOGEN=False,
        DEV_FERNET_KEY=None,
    ):
        errors = check_pan_encryption_key(None)
    assert len(errors) == 1
    assert errors[0].id == "folioman_app.security.E001"


# --- License keypair (ed25519) ----------------------------------------------


def test_generate_keypair_returns_distinct_valid_ed25519_keys():
    priv_b64, pub_b64 = generate_keypair()
    assert priv_b64 != pub_b64
    assert len(base64.b64decode(priv_b64)) == 32  # raw ed25519 private
    assert len(base64.b64decode(pub_b64)) == 32  # raw ed25519 public


def test_license_public_key_empty_by_default(monkeypatch):
    monkeypatch.delenv("FOLIOMAN_LICENSE_PUBLIC_KEY", raising=False)
    assert get_license_public_key_b64() == ""


def test_license_public_key_env_override(monkeypatch):
    monkeypatch.setenv("FOLIOMAN_LICENSE_PUBLIC_KEY", "  somebase64key  ")
    assert get_license_public_key_b64() == "somebase64key"
