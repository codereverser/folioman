"""PAN normalization, encryption, and a keyed lookup hash.

``pan_hash`` is an HMAC (not a plain SHA-256): a PAN is low-entropy and
structurally constrained, so a bare hash would be brute-forceable. Peppering it
with the Fernet key material — already the PAN-protection secret — makes the
lookup hash useless to anyone without the key, and lose-the-key already means
PANs are unrecoverable, so there is no new failure mode.
"""

from __future__ import annotations

import hashlib
import hmac

from folioman_app.security.keys import get_fernet, get_key_bytes


def normalize_pan(pan: str) -> str:
    return pan.strip().upper()


def mask_pan(pan: str) -> str:
    """Display form: keep the last 4 characters, mask the rest (``XXXXXX234F``).

    For showing *which* PAN a statement carries without exposing the full value
    (e.g. the import preview). Never the value we store — that is encrypted."""
    normalized = normalize_pan(pan)
    return ("X" * (len(normalized) - 4) + normalized[-4:]) if len(normalized) > 4 else normalized


def pan_hash(pan: str) -> str:
    """Keyed (HMAC-SHA256) hash of the normalized PAN, for equality lookup/dedup."""
    return hmac.new(get_key_bytes(), normalize_pan(pan).encode(), hashlib.sha256).hexdigest()


def encrypt_pan(pan: str) -> bytes:
    return get_fernet().encrypt(normalize_pan(pan).encode())


def decrypt_pan(token: bytes) -> str:
    return get_fernet().decrypt(bytes(token)).decode()
