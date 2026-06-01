"""Investor identity and folio references (no secrets — encryption lives in Django).

An ``Investor`` is the person whose money is tracked — self, a family member, an
HUF, or (in the advisor use case) a client. The Django ``app`` layer groups
investors into families and owns the multi-tenant ``investor_id`` scoping; this
core model is just the framework-free identity shell.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, field_validator, model_validator

from folioman_core.models.base import DomainModel


class FolioType(StrEnum):
    MF = "mf"
    DEMAT = "demat"


def normalize_folio_number(number: str) -> str:
    """Canonical folio identity for matching the same account across statements.

    Adopts the KFintech rendering as canonical. The digits after ``/`` are a
    sub-account / holding-mode suffix; the *default* sub-account is printed as
    ``"/0"`` by CAMS and a CDSL eCAS but omitted entirely by KFintech — so the
    same folio appears as ``"12345 / 0"`` / ``"12345/0"`` / ``"12345"`` across
    sources. We therefore:

    * remove all whitespace, and
    * drop a trailing ``"/0"`` (or ``"/00"`` — an all-zero default suffix),

    while **preserving any non-zero suffix** (``"12345/63"``, ``"12345/20"`` …),
    because that can distinguish legally distinct accounts (e.g. individual vs
    NRI/joint) and the full folio is the canonical transaction identity for them.

    Parsers stay faithful to the source document (they keep the raw string); this
    is applied only where folioman persists folio *identity* (``upsert_folio``).
    """
    compact = "".join(str(number).split())
    base, sep, suffix = compact.rpartition("/")
    if sep and (suffix == "" or set(suffix) <= {"0"}):
        return base
    return compact


class Folio(DomainModel):
    """MF folio or demat account belonging to an investor."""

    folio_type: FolioType
    number: str = Field(min_length=1, max_length=64)
    broker: str = Field(default="", max_length=64)
    amc_code: str = Field(default="", max_length=32)
    pan_kyc: bool = False

    @model_validator(mode="after")
    def validate_folio_rules(self) -> Self:
        if self.folio_type is FolioType.DEMAT and not self.broker:
            msg = "demat folio requires broker"
            raise ValueError(msg)
        return self


class Investor(DomainModel):
    """Investor identity shell — PAN handling deferred to the Django app layer."""

    name: str = Field(min_length=1, max_length=255)
    email: str = Field(min_length=3, max_length=254)
    is_huf: bool = False
    relation: str = Field(default="", max_length=20)
    folios: list[Folio] = Field(default_factory=list)

    @field_validator("relation", mode="before")
    @classmethod
    def normalize_relation(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value).strip().lower()
