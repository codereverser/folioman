"""Jurisdiction-pluggable capital-gains tax (India v1)."""

from folioman_core.tax.india import IndiaTaxPolicy
from folioman_core.tax.ltcg_stcg import compute_gain_lines
from folioman_core.tax.models import Disposal, GainLine, TaxYear, Term
from folioman_core.tax.policy import TaxPolicy, get_policy
from folioman_core.tax.schedule_112a import Schedule112ARow, compute_schedule_112a

__all__ = [
    "Disposal",
    "GainLine",
    "IndiaTaxPolicy",
    "Schedule112ARow",
    "TaxPolicy",
    "TaxYear",
    "Term",
    "compute_gain_lines",
    "compute_schedule_112a",
    "get_policy",
]
