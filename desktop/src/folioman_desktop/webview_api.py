"""The JS↔Python bridge exposed to the SPA as ``window.pywebview.api``.

The browser ``<input type=file>`` works inside the webview, but a native OS open
dialog is the expected desktop affordance — and on some platforms the in-page
picker is flaky. ``pick_cas_file`` opens a real native dialog and hands the chosen
file's *bytes* back to JS (base64), so the SPA can reconstruct a ``File`` and run
the exact same multipart upload path the hosted build uses — one import code path,
no desktop-only API endpoint.

The bytes round-trip is fine here: CAS PDFs are small (KBs to a couple MB) and the
file is read once, locally. Returning a path instead would force a second,
desktop-only read endpoint on the server; returning bytes keeps the contract one.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PDF_FILE_TYPES = ("PDF statement (*.pdf)", "All files (*.*)")
_TRADEBOOK_FILE_TYPES = ("Tradebook (*.csv;*.xlsx;*.xls)", "All files (*.*)")


class WebviewApi:
    """Methods on this object are callable from JS as ``window.pywebview.api.<name>``.

    ``bind_window`` is called once after the window exists so the dialog opens
    parented to it; the methods themselves are invoked on a webview-managed thread.
    """

    def __init__(self) -> None:
        self._window: Any = None

    def bind_window(self, window: Any) -> None:
        self._window = window

    def pick_cas_file(self) -> dict[str, str] | None:
        """Open a native open-dialog for a CAS PDF; return its name + base64 bytes.

        Returns ``None`` when the user cancels (JS sees ``null`` and no-ops). Reads
        and base64-encodes the file so the SPA can build a ``File`` for upload.
        """
        return self._pick_file(_PDF_FILE_TYPES)

    def pick_tradebook_file(self) -> dict[str, str] | None:
        """Open a native open-dialog for a broker tradebook (CSV/XLSX); return its
        name + base64 bytes (or ``None`` on cancel). Same bytes round-trip as
        ``pick_cas_file`` — the SPA rebuilds a ``File`` and runs the canonical-CSV
        import path; the in-page picker is flaky in the webview on some platforms."""
        return self._pick_file(_TRADEBOOK_FILE_TYPES)

    def pick_tradebook_files(self) -> list[dict[str, str]]:
        """Like ``pick_tradebook_file`` but allows selecting several at once — a
        broker (e.g. Zerodha) exports one tradebook per financial year, so a user
        commonly has many. Returns a list of ``{name, base64 bytes}`` (empty on
        cancel); the SPA merges them into one import."""
        return self._pick_files(_TRADEBOOK_FILE_TYPES, allow_multiple=True)

    def _pick_files(
        self, file_types: tuple[str, ...], *, allow_multiple: bool
    ) -> list[dict[str, str]]:
        """Open a native open-dialog filtered to ``file_types`` and return each
        picked file as ``{name, base64 bytes}``; empty list if cancelled. Unreadable
        files are skipped (logged), not fatal to the rest of the selection."""
        import webview

        # pywebview 5.x moved the dialog kind to the `FileDialog` enum; the old
        # `webview.OPEN_DIALOG` constant is deprecated (and slated for removal).
        # Prefer the enum, fall back for older pywebview.
        open_dialog = getattr(webview, "FileDialog", None)
        open_dialog = open_dialog.OPEN if open_dialog is not None else webview.OPEN_DIALOG

        window = self._window or webview.active_window()
        selection = window.create_file_dialog(
            open_dialog,
            allow_multiple=allow_multiple,
            file_types=file_types,
        )
        if not selection:  # cancelled → empty tuple / None
            return []
        picked: list[dict[str, str]] = []
        for raw in selection:
            path = Path(raw)
            try:
                data = path.read_bytes()
            except OSError:
                logger.exception("desktop: could not read picked file %s", path)
                continue
            picked.append({"name": path.name, "data": base64.b64encode(data).decode("ascii")})
        return picked

    def _pick_file(self, file_types: tuple[str, ...]) -> dict[str, str] | None:
        """Single-file convenience over :meth:`_pick_files`."""
        files = self._pick_files(file_types, allow_multiple=False)
        return files[0] if files else None
