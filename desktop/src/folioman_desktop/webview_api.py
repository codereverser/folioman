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
        import webview

        # pywebview 5.x moved the dialog kind to the `FileDialog` enum; the old
        # `webview.OPEN_DIALOG` constant is deprecated (and slated for removal).
        # Prefer the enum, fall back for older pywebview.
        open_dialog = getattr(webview, "FileDialog", None)
        open_dialog = open_dialog.OPEN if open_dialog is not None else webview.OPEN_DIALOG

        window = self._window or webview.active_window()
        selection = window.create_file_dialog(
            open_dialog,
            allow_multiple=False,
            file_types=_PDF_FILE_TYPES,
        )
        if not selection:  # cancelled → empty tuple / None
            return None
        path = Path(selection[0])
        try:
            data = path.read_bytes()
        except OSError:
            logger.exception("desktop: could not read picked file %s", path)
            return None
        return {"name": path.name, "data": base64.b64encode(data).decode("ascii")}

    def save_csv_file(self, default_filename: str, content: str) -> bool:
        """Open a native save-dialog for a CSV file and write `content` to it.

        Returns True if saved, False if cancelled or failed.
        """
        import webview

        open_dialog = getattr(webview, "FileDialog", None)
        save_dialog = open_dialog.SAVE if open_dialog is not None else webview.SAVE_DIALOG

        window = self._window or webview.active_window()
        selection = window.create_file_dialog(
            save_dialog,
            save_filename=default_filename,
            file_types=("CSV file (*.csv)", "All files (*.*)"),
        )
        if not selection:  # cancelled
            return False

        path = Path(selection) if isinstance(selection, str) else Path(selection[0])
        try:
            path.write_text(content, encoding="utf-8")
            return True
        except OSError:
            logger.exception("desktop: could not save file %s", path)
            return False
