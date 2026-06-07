"""The launcher's argv dispatch: GUI by default, headless refresh on demand.

We don't open a window or hit the network — both branches are stubbed; the test
just asserts the routing the OS scheduler relies on.
"""

from __future__ import annotations

from folioman_desktop import __main__ as launcher


def test_refresh_navs_arg_runs_headless(monkeypatch):
    calls = {"refresh": 0, "gui": 0}
    monkeypatch.setattr(launcher, "run_refresh_navs", lambda: calls.__setitem__("refresh", 1) or 0)
    monkeypatch.setattr(launcher, "run_gui", lambda: calls.__setitem__("gui", 1))

    rc = launcher.main(["refresh-navs"])

    assert rc == 0
    assert calls == {"refresh": 1, "gui": 0}  # headless only, never the window


def test_no_args_opens_gui(monkeypatch):
    calls = {"refresh": 0, "gui": 0}
    monkeypatch.setattr(launcher, "run_refresh_navs", lambda: calls.__setitem__("refresh", 1) or 0)
    monkeypatch.setattr(launcher, "run_gui", lambda: calls.__setitem__("gui", 1))

    rc = launcher.main([])

    assert rc == 0
    assert calls == {"refresh": 0, "gui": 1}
