"""Unified CAS PDF reader — one entry point for both statement kinds.

casparser already type-detects the upload (CAMS/KFin MF CAS -> ``CASData``,
NSDL/CDSL eCAS -> ``NSDLCASData``). ``read_cas`` parses once and dispatches to the
matching folioman mapper, so callers (and users) need a single "import a CAS"
step instead of choosing the kind up front.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

import casparser
from casparser.exceptions import IncorrectPasswordError, ParserException
from casparser.types import CASData, NSDLCASData

from folioman_core.ecas_parser import ecas_investor_identity, map_ecas_data
from folioman_core.models.cas import CasInvestorIdentity, EcasStatement, MfCasStatement
from folioman_core.parser import (
    CASParseError,
    CASPasswordError,
    map_cas_data,
    mf_investor_identity,
)


@dataclass(frozen=True, slots=True)
class ParsedCas:
    """Result of parsing any CAS PDF. Exactly one of ``mf`` / ``ecas`` is set.

    ``investor`` carries the owner identity — including the **full** PAN — used to
    resolve (or create) the investor the statement belongs to. It is PII: route it
    only into the encrypted ``Investor`` columns; never log or serialize it.
    """

    mf: MfCasStatement | None = None
    ecas: EcasStatement | None = None
    investor: CasInvestorIdentity = field(default_factory=CasInvestorIdentity)

    @property
    def is_ecas(self) -> bool:
        return self.ecas is not None


def read_cas(file: str | io.IOBase, password: str) -> ParsedCas:
    """Parse any CAS PDF, auto-detecting MF CAS vs NSDL/CDSL eCAS.

    Raises ``CASPasswordError`` for a wrong password and ``CASParseError`` for an
    unparseable / unsupported document.
    """
    try:
        data = casparser.read_cas_pdf(file, password, output="dict")
    except IncorrectPasswordError as exc:
        raise CASPasswordError(str(exc)) from exc
    except ParserException as exc:
        raise CASParseError(str(exc)) from exc
    if isinstance(data, CASData):
        return ParsedCas(mf=map_cas_data(data), investor=mf_investor_identity(data))
    if isinstance(data, NSDLCASData):
        return ParsedCas(ecas=map_ecas_data(data), investor=ecas_investor_identity(data))
    ft = getattr(data, "file_type", "?")
    msg = f"unsupported CAS file_type {ft!r}"
    raise CASParseError(msg)
