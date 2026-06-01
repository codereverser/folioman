"""manage.py export_openapi — write the Ninja OpenAPI schema as JSON.

This is the headless backend's API contract: the frontend generates its typed
client from it (and a test asserts the committed copy stays in sync). Run
`make openapi` to regenerate the committed file after changing any route or
schema. The schema is derived purely from the code (routes + schemas), so it is
stable across run modes — auth mode does not change it.
"""

from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Export the Ninja OpenAPI schema as JSON (stdout, or a file via --output)."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--output", "-o", default=None, help="File path to write; prints to stdout if omitted."
        )

    def handle(self, *args, **options) -> None:
        from folioman_app.api.main import api

        schema = api.get_openapi_schema()
        # sort_keys keeps the committed file diff-stable across regenerations.
        text = json.dumps(schema, indent=2, sort_keys=True) + "\n"

        output = options["output"]
        if not output:
            self.stdout.write(text)
            return
        Path(output).write_text(text, encoding="utf-8")
        self.stdout.write(
            self.style.SUCCESS(f"Wrote OpenAPI schema to {output} ({len(schema['paths'])} paths)")
        )
