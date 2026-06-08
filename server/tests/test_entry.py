"""Server entrypoint: env→gunicorn options builder + subcommand dispatch.

These exercise the pure parts only — `gunicorn_options` imports no gunicorn and
touches no I/O, and the dispatch tests stub out the serve/migrate side effects —
so the suite runs without the server extra (gunicorn/psycopg) installed.
"""

from __future__ import annotations

import folioman_server.__main__ as entry


def test_defaults_bind_all_interfaces_on_8000():
    opts = entry.gunicorn_options({})
    assert opts["bind"] == "0.0.0.0:8000"
    assert opts["worker_class"] == "gthread"
    assert opts["threads"] == 4
    assert opts["timeout"] == 120
    assert opts["accesslog"] == "-"
    assert opts["errorlog"] == "-"


def test_host_and_port_compose_the_bind():
    opts = entry.gunicorn_options({"FOLIOMAN_HOST": "127.0.0.1", "FOLIOMAN_PORT": "9001"})
    assert opts["bind"] == "127.0.0.1:9001"


def test_explicit_bind_wins_over_host_port():
    opts = entry.gunicorn_options(
        {"FOLIOMAN_HOST": "127.0.0.1", "FOLIOMAN_PORT": "9001", "FOLIOMAN_BIND": "unix:/run/f.sock"}
    )
    assert opts["bind"] == "unix:/run/f.sock"


def test_web_concurrency_sets_workers():
    assert entry.gunicorn_options({"WEB_CONCURRENCY": "5"})["workers"] == 5


def test_folioman_workers_used_when_web_concurrency_absent():
    assert entry.gunicorn_options({"FOLIOMAN_WORKERS": "3"})["workers"] == 3


def test_invalid_and_nonpositive_ints_fall_back_to_default():
    # garbage and <=0 both fall back rather than crashing the boot
    assert entry.gunicorn_options({"FOLIOMAN_THREADS": "abc"})["threads"] == 4
    assert entry.gunicorn_options({"FOLIOMAN_TIMEOUT": "0"})["timeout"] == 120


def test_worker_class_and_loglevel_overridable():
    opts = entry.gunicorn_options({"FOLIOMAN_WORKER_CLASS": "sync", "FOLIOMAN_LOG_LEVEL": "debug"})
    assert opts["worker_class"] == "sync"
    assert opts["loglevel"] == "debug"


def test_main_default_serves(monkeypatch):
    calls = []
    monkeypatch.setattr(entry, "_serve", lambda: calls.append("serve"))
    monkeypatch.setattr(entry, "_migrate", lambda: calls.append("migrate"))
    assert entry.main([]) == 0
    assert calls == ["serve"]


def test_main_migrate_subcommand(monkeypatch):
    calls = []
    monkeypatch.setattr(entry, "_serve", lambda: calls.append("serve"))
    monkeypatch.setattr(entry, "_migrate", lambda: calls.append("migrate"))
    assert entry.main(["migrate"]) == 0
    assert calls == ["migrate"]


def test_main_run_scheduler_subcommand(monkeypatch):
    calls = []
    monkeypatch.setattr(entry, "_serve", lambda: calls.append("serve"))
    monkeypatch.setattr(entry, "_run_scheduler", lambda: calls.append("scheduler"))
    assert entry.main(["run-scheduler"]) == 0
    assert calls == ["scheduler"]


def test_main_setup_banner_subcommand(monkeypatch):
    calls = []
    monkeypatch.setattr(entry, "_serve", lambda: calls.append("serve"))
    monkeypatch.setattr(entry, "_setup_banner", lambda: calls.append("banner"))
    assert entry.main(["setup-banner"]) == 0
    assert calls == ["banner"]


def test_main_unknown_arg_serves(monkeypatch):
    calls = []
    monkeypatch.setattr(entry, "_serve", lambda: calls.append("serve"))
    monkeypatch.setattr(entry, "_migrate", lambda: calls.append("migrate"))
    assert entry.main(["--anything"]) == 0
    assert calls == ["serve"]
