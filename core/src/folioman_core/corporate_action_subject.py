"""Parse an NSE/BSE corporate-action ``subject`` (free text) into a typed event.

The exchange corporate-action feeds describe each action only as a free-text
``subject`` — e.g. ``"Bonus 3:1"``, ``"Face Value Split From Rs 10 to Rs 2"``,
``"Interim Dividend - Rs 1.10 Per Share"``, ``"Demerger"``, ``"Buy Back"``,
``"Scheme of Arrangement"``. This turns that string into a
:class:`ParsedCorporateAction` carrying the type plus whatever ratio/amount is
extractable, so the detection pass (E10.3) can match a clean unit ratio against
the eCAS anchor.

Conservative by design: anything not confidently parsed — an unknown phrase, a
bonus/split with no readable ratio, a merger/demerger/buyback (the ratio is never
in the subject) — is returned with ``needs_review=True`` and **never guessed**.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum


class CorpActionType(StrEnum):
    DIVIDEND = "dividend"
    BONUS = "bonus"
    SPLIT = "split"  # incl. face-value sub-division
    RIGHTS = "rights"
    BUYBACK = "buyback"
    MERGER = "merger"
    DEMERGER = "demerger"
    SCHEME = "scheme_of_arrangement"
    IDENTITY = "identity"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ParsedCorporateAction:
    """A corporate-action subject, classified.

    ``unit_multiplier`` is the factor the holding scales by — the number the
    detection pass compares against ``eCAS / ledger-net`` (bonus ``3:1`` → ``4``;
    a ``Rs 10 → Rs 2`` split → ``5``). ``None`` when the action doesn't scale units
    (dividend) or the ratio isn't in the subject (merger/demerger). ``ratio`` is
    the raw ``a:b`` for bonus/rights; ``amount`` is the per-share dividend.
    """

    type: CorpActionType
    raw: str
    ratio: tuple[int, int] | None = None
    unit_multiplier: Decimal | None = None
    amount: Decimal | None = None
    face_value_from: Decimal | None = None
    face_value_to: Decimal | None = None
    needs_review: bool = False


_RATIO = re.compile(r"(\d+)\s*:\s*(\d+)")
# "Rs 10", "Rs.10/-", "INR 5", "₹2" → the number. Tolerant of the trailing "/-".
_RS_NUM = re.compile(r"(?:rs\.?|inr|₹)\s*(\d+(?:\.\d+)?)", re.IGNORECASE)
# A face-value split states the old → new face value, with or without a "face value"
# label and in assorted spellings: "Face Value Split From Rs 10 to Rs 2", "Stock Split
# From Rs.2/- to Rs.1/-", "... From Rs 2/- Per Share To Re 1/- Per Share". The currency
# token (Rs/Rs./Re/INR/₹) and the trailing "/-" are optional; the unit factor is
# old / new. Gated on split context at the call site so it can't catch a stray
# "from … to …" in another action.
_FV_FROM_TO = re.compile(
    r"from\s+(?:rs\.?|re\.?|inr|₹)?\s*(\d+(?:\.\d+)?)\s*(?:/-)?"
    r".*?\bto\s+(?:rs\.?|re\.?|inr|₹)?\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE | re.DOTALL,
)


def _decimal(text: str) -> Decimal | None:
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


_MAX_SUBJECT_LEN = 512


def parse_subject(subject: str) -> ParsedCorporateAction:
    """Classify a corporate-action ``subject`` string."""
    raw = (subject or "")[:_MAX_SUBJECT_LEN]
    s = " ".join(raw.lower().split())
    if not s:
        return ParsedCorporateAction(CorpActionType.UNKNOWN, raw, needs_review=True)

    if "reverse split" in s or "reverse stock split" in s or "consolidation" in s:
        return ParsedCorporateAction(CorpActionType.SPLIT, raw, needs_review=True)

    # Face-value split ("Face Value Split From Rs 10 to Rs 2", "Stock Split From
    # Rs.2/- to Rs.1/-"): a stock split where the unit factor is old / new face value.
    fv = _FV_FROM_TO.search(s) if ("split" in s or "sub" in s) else None
    if fv:
        frm, to = _decimal(fv.group(1)), _decimal(fv.group(2))
        if frm is not None and to and frm > to:
            return ParsedCorporateAction(
                CorpActionType.SPLIT,
                raw,
                unit_multiplier=frm / to,
                face_value_from=frm,
                face_value_to=to,
                needs_review=False,
            )
        # Not a clean old>new reduction — fall through to the generic split handling.

    if "bonus" in s:
        m = _RATIO.search(s)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            # Bonus a:b → a new shares per b held → holding scales by (a+b)/b.
            mult = (Decimal(a) + Decimal(b)) / Decimal(b) if b else None
            return ParsedCorporateAction(
                CorpActionType.BONUS,
                raw,
                ratio=(a, b),
                unit_multiplier=mult,
                needs_review=mult is None,
            )
        return ParsedCorporateAction(CorpActionType.BONUS, raw, needs_review=True)

    if "dividend" in s:
        m = _RS_NUM.search(s)
        amount = _decimal(m.group(1)) if m else None
        return ParsedCorporateAction(
            CorpActionType.DIVIDEND, raw, amount=amount, needs_review=amount is None
        )

    # Generic split / sub-division stated as a ratio ("Stock Split 5:1").
    if "split" in s or "sub-division" in s or "subdivision" in s:
        m = _RATIO.search(s)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            mult = Decimal(a) / Decimal(b) if b else None
            return ParsedCorporateAction(
                CorpActionType.SPLIT,
                raw,
                ratio=(a, b),
                unit_multiplier=mult,
                needs_review=mult is None,
            )
        return ParsedCorporateAction(CorpActionType.SPLIT, raw, needs_review=True)

    if "rights" in s:
        m = _RATIO.search(s)
        ratio = (int(m.group(1)), int(m.group(2))) if m else None
        # The cash leg (issue price) isn't reliably in the subject → manual.
        return ParsedCorporateAction(CorpActionType.RIGHTS, raw, ratio=ratio, needs_review=True)

    if "buy back" in s or "buyback" in s:
        return ParsedCorporateAction(CorpActionType.BUYBACK, raw, needs_review=True)
    if "demerger" in s:
        return ParsedCorporateAction(CorpActionType.DEMERGER, raw, needs_review=True)
    if any(
        phrase in s
        for phrase in (
            "change of name",
            "change in name",
            "change of symbol",
            "change in symbol",
            "name change",
            "symbol change",
            "isin change",
        )
    ):
        return ParsedCorporateAction(CorpActionType.IDENTITY, raw, needs_review=True)
    if "scheme of arrangement" in s:
        return ParsedCorporateAction(CorpActionType.SCHEME, raw, needs_review=True)
    if "merger" in s or "amalgamation" in s:
        return ParsedCorporateAction(CorpActionType.MERGER, raw, needs_review=True)

    return ParsedCorporateAction(CorpActionType.UNKNOWN, raw, needs_review=True)
