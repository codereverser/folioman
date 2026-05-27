"""Phase 1 toolchain smoke test — proves the package imports and pytest runs."""

import folioman_core


def test_version_exposed():
    assert folioman_core.__version__ == "0.0.0"
