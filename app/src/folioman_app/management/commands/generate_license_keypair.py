"""Generate the ed25519 license-signing keypair (run once, by the developer).

Writes the PRIVATE key to a git-ignored ``secrets/`` file (0600) and prints the
PUBLIC key. The private key signs ``.license`` files (issue_license); the
public key is distributed via FOLIOMAN_LICENSE_PUBLIC_KEY or
licensing/keys.py::EMBEDDED_LICENSE_PUBLIC_KEY_B64 so the app can verify them.

This deliberately never prints the private key to stdout (it would leak into
shell history / logs) and never commits it.
"""

from __future__ import annotations

import stat
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from folioman_app.licensing.keys import generate_keypair


class Command(BaseCommand):
    help = "Generate an ed25519 license-signing keypair (developer, once)."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--out",
            default="secrets/license_private_key.b64",
            help="Path to write the private key (git-ignored).",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Overwrite an existing private key file.",
        )

    def handle(self, *args, **options) -> None:
        out = Path(options["out"])
        if out.exists() and not options["force"]:
            raise CommandError(
                f"{out} already exists. Refusing to overwrite a signing key "
                "without --force (this would invalidate every issued license)."
            )

        private_b64, public_b64 = generate_keypair()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(private_b64 + "\n")
        out.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0o600

        self.stdout.write(self.style.SUCCESS(f"Private key written to {out} (keep it offline)."))
        self.stdout.write("")
        self.stdout.write("Public key (distribute this — set FOLIOMAN_LICENSE_PUBLIC_KEY or paste")
        self.stdout.write("into licensing/keys.py::EMBEDDED_LICENSE_PUBLIC_KEY_B64):")
        self.stdout.write("")
        self.stdout.write(public_b64)
