"""The JS↔Python file-picker bridge (``window.pywebview.api``).

No real GUI is opened — the window's native dialog is stubbed — so we assert the
contract the SPA relies on: a picked file comes back as ``{name, base64 bytes}``,
and a cancel comes back as ``None``.
"""

from __future__ import annotations

import base64

from folioman_desktop.webview_api import WebviewApi


class _FakeWindow:
    """Stands in for a PyWebView window: records the dialog call and returns a
    canned selection (a tuple of paths, or None/empty for a cancel)."""

    def __init__(self, selection):
        self._selection = selection
        self.calls: list[dict] = []

    def create_file_dialog(self, dialog_type, **kwargs):
        self.calls.append({"dialog_type": dialog_type, **kwargs})
        return self._selection


def test_pick_returns_name_and_base64_bytes(tmp_path):
    pdf = tmp_path / "statement.pdf"
    pdf.write_bytes(b"%PDF-1.7 payload")
    api = WebviewApi()
    api.bind_window(_FakeWindow((str(pdf),)))

    result = api.pick_cas_file()

    assert result is not None
    assert result["name"] == "statement.pdf"
    assert base64.b64decode(result["data"]) == b"%PDF-1.7 payload"


def test_pick_returns_none_on_cancel():
    api = WebviewApi()
    api.bind_window(_FakeWindow(None))  # user closed the dialog without choosing
    assert api.pick_cas_file() is None


def test_pick_tradebook_returns_bytes_and_filters_to_tradebook(tmp_path):
    book = tmp_path / "tradebook.csv"
    book.write_bytes(b"symbol,qty\nRELIANCE,10\n")
    api = WebviewApi()
    window = _FakeWindow((str(book),))
    api.bind_window(window)

    result = api.pick_tradebook_file()

    assert result is not None
    assert result["name"] == "tradebook.csv"
    assert base64.b64decode(result["data"]) == b"symbol,qty\nRELIANCE,10\n"
    assert "csv" in window.calls[0]["file_types"][0].lower()


def test_pick_single_selection_only(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"x")
    api = WebviewApi()
    window = _FakeWindow((str(pdf),))
    api.bind_window(window)

    api.pick_cas_file()

    assert window.calls[0]["allow_multiple"] is False
    # Use the non-deprecated FileDialog enum (not the legacy webview.OPEN_DIALOG).
    import webview

    assert window.calls[0]["dialog_type"] == webview.FileDialog.OPEN
