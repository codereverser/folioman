#!/usr/bin/env python
"""Django management entry point for the Folioman app.

Defaults to the shared `base` settings; override with DJANGO_SETTINGS_MODULE
(e.g. `folioman_app.settings.desktop` or `...server`).
"""

from __future__ import annotations

import os
import sys


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "folioman_app.settings.base")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
