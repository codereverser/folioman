"""OS-level scheduler templates + installer for the NAV-refresh job.

The per-OS templates (launchd plist, systemd timer/service, Windows Task XML)
and ``install.py`` register a daily run of the binary's ``refresh-navs``
subcommand, keeping NAVs current while the app is closed.
"""
