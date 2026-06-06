"""First-run bootstrap helpers (the parts testable without a second django.setup).

The full ``bootstrap()`` flips ``DJANGO_SETTINGS_MODULE`` and migrates a fresh DB,
which fights the already-configured pytest session; it's exercised end-to-end by
the launcher smoke path instead. Here we pin down the pure, idempotent helpers.
"""

from __future__ import annotations

import os

from folioman_desktop import bootstrap as boot


def test_resolve_data_dir_creates_dir_and_logs(tmp_path, monkeypatch):
    target = tmp_path / "userdata"
    monkeypatch.setenv("FOLIOMAN_DATA_DIR", str(target))

    resolved = boot.resolve_data_dir()

    assert resolved == target
    assert target.is_dir()
    assert (target / "logs").is_dir()  # log handler can open its file on first record
    # The resolved path is pinned back so settings.desktop resolves identically.
    assert os.environ["FOLIOMAN_DATA_DIR"] == str(target)


def test_resolve_data_dir_is_idempotent(tmp_path, monkeypatch):
    target = tmp_path / "userdata"
    monkeypatch.setenv("FOLIOMAN_DATA_DIR", str(target))
    boot.resolve_data_dir()
    # A second launch over an existing dir must not raise (exist_ok).
    assert boot.resolve_data_dir() == target


def test_ensure_settings_module_defaults_to_desktop(monkeypatch):
    monkeypatch.delenv("DJANGO_SETTINGS_MODULE", raising=False)
    boot.ensure_settings_module()
    assert os.environ["DJANGO_SETTINGS_MODULE"] == boot.DEFAULT_SETTINGS


def test_ensure_settings_module_respects_an_explicit_override(monkeypatch):
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "folioman_app.settings.server")
    boot.ensure_settings_module()  # setdefault → must not clobber a deliberate choice
    assert os.environ["DJANGO_SETTINGS_MODULE"] == "folioman_app.settings.server"
