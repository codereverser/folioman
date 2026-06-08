#!/bin/sh
# Apply database migrations, then hand off to the container command (gunicorn).
# migrate is idempotent and takes a DB-level lock, so it is safe to run on every
# boot. `db` is gated healthy by compose before this runs.
set -e

# First-run setup token (LAN browser-setup hardening): pin the operator's value if
# they set one, else autogenerate. Exporting it *before* `exec` means every
# gunicorn worker validates against the same token (per-worker generation would
# diverge). `:-` substitutes when unset OR empty.
export FOLIOMAN_SETUP_TOKEN="${FOLIOMAN_SETUP_TOKEN:-$(python -c 'import secrets; print(secrets.token_urlsafe(24))')}"

python -m folioman_server migrate
python -m folioman_server setup-banner   # prints the token + /setup URL only if setup is pending
exec "$@"
