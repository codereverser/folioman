# Building Folioman locally

> **Placeholder.** The full build-from-source workflow is not yet implemented.
> This file currently captures the intended prerequisites and entry points only.

## Prerequisites

- Python 3.12+
- Node 20+ (pnpm preferred)
- Git
- `uv` (recommended) or `pip`

## Quick start (target shape)

```bash
git clone https://github.com/codereverser/folioman
cd folioman
make install        # uv sync + pnpm install across the workspace
make test           # core/ + app/ pytest, frontend unit tests
make desktop        # produces ./dist/folioman[.exe]
```

## Install paths (planned)

1. **Docker Compose** — recommended for self-hosted PM Pro
2. **Build from source** — `make desktop` for a native window
3. **`pip install folioman-cli`** — power users / CA scripting

Detailed per-platform instructions will land with the desktop packaging work.
