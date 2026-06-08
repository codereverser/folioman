"""Package layout smoke tests."""

import folioman_core
from folioman_core.models import SecurityType


def test_version_exposed():
    assert folioman_core.__version__ == "1.0.0"


def test_models_package_reexports():
    assert SecurityType.MF.value == "mf"
    expected = {
        "Security",
        "Transaction",
        "Holding",
        "Investor",
        "MfCasStatement",
        "EcasStatement",
    }
    assert expected.issubset(set(folioman_core.models.__all__))
