"""The OS NAV-refresh scheduler installer.

The actual launchctl/systemctl/schtasks calls touch the system, so they're not
exercised here; we lock down the pure pieces: template rendering leaves no
placeholders, every shipped template renders, time parsing, and OS dispatch.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from folioman_desktop.scheduler import install

_TEMPLATES = ("macos.plist", "linux.service", "linux.timer", "windows.xml")


def test_render_fills_all_placeholders():
    out = install.render_template(
        "@@EXECUTABLE@@ @@HOUR@@:@@MINUTE@@ @@LOG@@",
        executable="/Applications/Folioman.app/Contents/MacOS/folioman",
        hour=9,
        minute=5,
        log="/tmp/x.log",
    )
    # Zero-padded time, no leftover markers.
    assert out == "/Applications/Folioman.app/Contents/MacOS/folioman 09:05 /tmp/x.log"
    assert "@@" not in out


@pytest.mark.parametrize("name", _TEMPLATES)
def test_every_shipped_template_renders_clean(name):
    text = (Path(install.__file__).parent / name).read_text(encoding="utf-8")
    rendered = install.render_template(text, executable="/opt/folioman", hour=20, minute=0)
    assert "@@" not in rendered  # no unfilled placeholder slips into a real config


# The timer references the service (not the binary), so only these invoke the command.
@pytest.mark.parametrize("name", ("macos.plist", "linux.service", "windows.xml"))
def test_command_templates_invoke_the_binary(name):
    text = (Path(install.__file__).parent / name).read_text(encoding="utf-8")
    rendered = install.render_template(text, executable="/opt/folioman", hour=20, minute=0)
    assert "/opt/folioman" in rendered
    assert "refresh-navs" in rendered  # the headless subcommand the OS scheduler runs


def test_parse_time_accepts_hh_mm_and_rejects_garbage():
    assert install._parse_time("8:30") == (8, 30)
    assert install._parse_time("20") == (20, 0)
    for bad in ("25:00", "10:75", "abc"):
        with pytest.raises(ValueError):
            install._parse_time(bad)


def test_install_requires_executable(monkeypatch):
    monkeypatch.setattr(install.platform, "system", lambda: "Darwin")
    with pytest.raises(SystemExit):  # argparse error → SystemExit
        install.main([])  # no --executable


def test_uninstall_dispatches_per_os(monkeypatch):
    monkeypatch.setattr(install.platform, "system", lambda: "Linux")
    called = {"n": 0}
    # The dispatch table captured the real fns at import, so patch the entry itself.
    monkeypatch.setitem(install._UNINSTALLERS, "Linux", lambda: called.__setitem__("n", 1))
    assert install.main(["--uninstall"]) == 0
    assert called["n"] == 1


def test_unsupported_os_errors(monkeypatch):
    monkeypatch.setattr(install.platform, "system", lambda: "Plan9")
    with pytest.raises(SystemExit):
        install.main(["--uninstall"])
