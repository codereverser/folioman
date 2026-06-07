"""One-shot installer for the OS-level NAV-refresh schedule.

The desktop app refreshes NAVs in-process while it's open, but a portfolio
tracker is mostly *closed*. This registers a per-OS scheduled job that runs the
binary's ``refresh-navs`` subcommand daily, so prices stay current regardless.

Usage (run from a source checkout / the venv)::

    python -m folioman_desktop.scheduler.install --executable /path/to/folioman
    python -m folioman_desktop.scheduler.install --uninstall

``--executable`` is the packaged binary (e.g.
``/Applications/Folioman.app/Contents/MacOS/folioman`` on macOS). The per-OS
templates live beside this file; the rendering is kept pure (and tested) while
the install/uninstall steps shell out to ``launchctl`` / ``systemctl`` /
``schtasks``.
"""

from __future__ import annotations

import argparse
import platform
import subprocess
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_LABEL = "in.folioman.navrefresh"  # macOS launchd label
_LINUX_UNIT = "folioman-navrefresh"  # systemd unit base name
_WIN_TASK = "Folioman-NAVRefresh"  # Windows Task Scheduler name


def render_template(text: str, *, executable: str, hour: int, minute: int, log: str = "") -> str:
    """Fill a template's ``@@KEY@@`` placeholders. Pure — easy to test.

    ``HOUR``/``MINUTE`` are zero-padded to two digits so the systemd ``OnCalendar``
    and Windows ``StartBoundary`` time strings are well-formed.
    """
    return (
        text.replace("@@EXECUTABLE@@", executable)
        .replace("@@HOUR@@", f"{hour:02d}")
        .replace("@@MINUTE@@", f"{minute:02d}")
        .replace("@@LOG@@", log)
    )


def _read(name: str) -> str:
    return (_HERE / name).read_text(encoding="utf-8")


def _run(cmd: list[str]) -> None:
    print("$", " ".join(cmd))
    subprocess.run(cmd, check=True)


# --- macOS (launchd) --------------------------------------------------------


def _macos_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_LABEL}.plist"


def install_macos(executable: str, hour: int, minute: int) -> None:
    from platformdirs import user_log_dir

    log_dir = Path(user_log_dir("folioman", "folioman"))
    log_dir.mkdir(parents=True, exist_ok=True)
    rendered = render_template(
        _read("macos.plist"),
        executable=executable,
        hour=hour,
        minute=minute,
        log=str(log_dir / "navrefresh.log"),
    )
    target = _macos_plist_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rendered, encoding="utf-8")
    subprocess.run(["launchctl", "unload", str(target)], check=False)  # ignore if absent
    _run(["launchctl", "load", str(target)])
    print(f"Installed launchd agent → {target}")


def uninstall_macos() -> None:
    target = _macos_plist_path()
    subprocess.run(["launchctl", "unload", str(target)], check=False)
    target.unlink(missing_ok=True)
    print(f"Removed launchd agent ({target})")


# --- Linux (systemd user units) ---------------------------------------------


def _linux_unit_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def install_linux(executable: str, hour: int, minute: int) -> None:
    unit_dir = _linux_unit_dir()
    unit_dir.mkdir(parents=True, exist_ok=True)
    (unit_dir / f"{_LINUX_UNIT}.service").write_text(
        render_template(_read("linux.service"), executable=executable, hour=hour, minute=minute),
        encoding="utf-8",
    )
    (unit_dir / f"{_LINUX_UNIT}.timer").write_text(
        render_template(_read("linux.timer"), executable=executable, hour=hour, minute=minute),
        encoding="utf-8",
    )
    _run(["systemctl", "--user", "daemon-reload"])
    _run(["systemctl", "--user", "enable", "--now", f"{_LINUX_UNIT}.timer"])
    print(f"Installed systemd user timer → {unit_dir}/{_LINUX_UNIT}.timer")


def uninstall_linux() -> None:
    subprocess.run(["systemctl", "--user", "disable", "--now", f"{_LINUX_UNIT}.timer"], check=False)
    unit_dir = _linux_unit_dir()
    (unit_dir / f"{_LINUX_UNIT}.service").unlink(missing_ok=True)
    (unit_dir / f"{_LINUX_UNIT}.timer").unlink(missing_ok=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    print("Removed systemd user timer")


# --- Windows (Task Scheduler) -----------------------------------------------


def install_windows(executable: str, hour: int, minute: int) -> None:
    import tempfile

    rendered = render_template(
        _read("windows.xml"), executable=executable, hour=hour, minute=minute
    )
    # schtasks reads the XML from a file; it expects UTF-16 (declared in the XML).
    with tempfile.NamedTemporaryFile("w", suffix=".xml", encoding="utf-16", delete=False) as fh:
        fh.write(rendered)
        xml_path = fh.name
    _run(["schtasks", "/create", "/tn", _WIN_TASK, "/xml", xml_path, "/f"])
    print(f"Installed scheduled task '{_WIN_TASK}'")


def uninstall_windows() -> None:
    subprocess.run(["schtasks", "/delete", "/tn", _WIN_TASK, "/f"], check=False)
    print(f"Removed scheduled task '{_WIN_TASK}'")


# --- dispatch ---------------------------------------------------------------

_INSTALLERS = {"Darwin": install_macos, "Linux": install_linux, "Windows": install_windows}
_UNINSTALLERS = {"Darwin": uninstall_macos, "Linux": uninstall_linux, "Windows": uninstall_windows}


def _parse_time(value: str) -> tuple[int, int]:
    hour, _, minute = value.partition(":")
    h, m = int(hour), int(minute or 0)
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError
    return h, m


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install the OS NAV-refresh schedule.")
    parser.add_argument("--executable", help="Path to the Folioman binary (required to install).")
    parser.add_argument(
        "--time", default="20:00", help="Daily run time, HH:MM local (default 20:00)."
    )
    parser.add_argument("--uninstall", action="store_true", help="Remove the schedule instead.")
    args = parser.parse_args(argv)

    os_name = platform.system()
    if os_name not in _INSTALLERS:
        parser.error(f"Unsupported OS: {os_name}")

    if args.uninstall:
        _UNINSTALLERS[os_name]()
        return 0

    if not args.executable:
        parser.error("--executable is required to install (path to the Folioman binary).")
    if not Path(args.executable).exists():
        parser.error(f"Executable not found: {args.executable}")
    try:
        hour, minute = _parse_time(args.time)
    except ValueError:
        parser.error(f"Invalid --time {args.time!r}; expected HH:MM.")

    _INSTALLERS[os_name](args.executable, hour, minute)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
