"""License-signing keypair (ed25519).

The PRIVATE key is the developer's secret — generated once via
``manage.py generate_license_keypair``, kept offline (password manager / the
git-ignored ``secrets/`` dir), and NEVER committed. Only the PUBLIC key is
distributed so the app can verify ``.license`` files.

The public key is sourced from ``FOLIOMAN_LICENSE_PUBLIC_KEY`` (base64) or, as a
fallback, the ``EMBEDDED_LICENSE_PUBLIC_KEY_B64`` constant which the developer
fills in after running keygen. Empty in both ⇒ licensing is not configured and
everything stays on the free tier.
"""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from folioman_app._env import env

# Filled in by the developer after `generate_license_keypair`. Intentionally
# empty in the repo — no real signing public key is committed by tooling.
EMBEDDED_LICENSE_PUBLIC_KEY_B64 = ""


def get_license_public_key_b64() -> str:
    """Base64 ed25519 public key, or '' when licensing is not configured."""
    return env.str("FOLIOMAN_LICENSE_PUBLIC_KEY", EMBEDDED_LICENSE_PUBLIC_KEY_B64).strip()


def generate_keypair() -> tuple[str, str]:
    """Generate an ed25519 keypair. Returns (private_b64, public_b64) raw keys."""
    private_key = Ed25519PrivateKey.generate()
    private_b64 = base64.b64encode(private_key.private_bytes_raw()).decode()
    public_b64 = base64.b64encode(private_key.public_key().public_bytes_raw()).decode()
    return private_b64, public_b64
