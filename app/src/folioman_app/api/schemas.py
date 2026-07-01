"""Request/response schemas for the Ninja API.

Never expose PAN material — only a ``has_pan`` boolean. PAN comes IN as plaintext
(``pan``) and is encrypted server-side; it is never returned.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from ninja import Schema
from pydantic import Field


class InvestorIn(Schema):
    name: str
    email: str = ""
    family_id: int | None = None
    pan: str | None = None  # plaintext in; encrypted server-side; never returned
    # is_huf / relation aren't part of v1 and the UI can't set them, so they're not
    # accepted on input — the model owns their defaults (False / ""). Still read
    # back on InvestorOut for forward-compat.


class InvestorUpdate(Schema):
    # All optional — PATCH applies only the fields present in the request body.
    name: str | None = None
    email: str | None = None
    family_id: int | None = None  # present+null clears the family (move to solo)
    pan: str | None = None  # present+null/'' clears the PAN


class InvestorOut(Schema):
    id: int
    name: str
    email: str
    is_huf: bool
    relation: str
    family_id: int | None
    has_pan: bool
    # True once statements are imported under this investor — the PAN is then the
    # join key for those imports and is locked (changing it would strand the data).
    pan_locked: bool
    created_at: datetime
    updated_at: datetime


class InvestorDetailOut(InvestorOut):
    # Single-investor read only: a masked PAN (last 4) to disambiguate similar names.
    # Kept off the roster list so we don't decrypt every investor on each load.
    pan_masked: str


class FolioOut(Schema):
    id: int
    folio_type: str
    number: str
    broker: str
    amc_code: str
    pan_kyc: bool


class FamilyIn(Schema):
    name: str


class FamilyOut(Schema):
    id: int
    name: str
    investor_count: int
    created_at: datetime


class FamilyDetailOut(FamilyOut):
    investors: list[InvestorOut]


class AssetMixRow(Schema):
    security_type: str
    value_inr: Decimal


class AllocationBucket(Schema):
    """One slice of a sub-asset-class allocation breakdown (by AMC, by category)."""

    label: str
    value_inr: Decimal


class HoldingValueRow(Schema):
    security_id: int
    name: str
    security_type: str
    symbol: str = ""  # exchange ticker for equities/ETFs/bonds; "" for MFs
    amc: str = ""  # fund house — groups the per-fund breakdown
    category: str = ""  # equity / debt — groups the per-fund breakdown
    units: Decimal
    value_inr: Decimal | None
    invested_inr: Decimal | None = None  # FIFO cost basis of the units still held
    latest_nav: Decimal | None = None  # current NAV the units are valued at
    return_pct: float | None = None  # (value - invested) / invested, as a fraction
    # money-weighted annualized return of this fund alone (its cashflows + current
    # value), as a fraction. None for snapshot-only or held-but-unpriced holdings.
    xirr: float | None = None
    day_change_inr: Decimal | None = None  # units * (latest NAV - prior NAV)
    day_change_pct: float | None = None  # (latest NAV - prior NAV) / prior NAV


class InvestorRowOut(Schema):
    """One roster-list row — the lean fields the Investors page shows, computed in
    the single bulk aggregate pass (no per-investor /summary fan-out)."""

    investor_id: int
    as_of: date
    total_inr: Decimal
    is_provisional: bool = False
    holdings_count: int
    integrity_unit_count: int
    tax_ready_count: int
    needs_attention_count: int
    snapshot_count: int
    # The "⚠ N unpriced" marker needs a live valuation to know which held MF lack a
    # NAV; the lean roster doesn't value, so it's omitted here (still shown on the
    # dashboard). Restore by persisting the count at recompute time.
    unpriced_fund_count: int = 0
    last_import_at: datetime | None = None


class RosterAggregateOut(Schema):
    """Advisor-wide roster header (net worth + counts + integrity roll-up) plus a
    lean per-investor row for the list — one request for the whole landing page."""

    as_of: date
    total_inr: Decimal
    investor_count: int
    family_count: int
    integrity_unit_count: int  # (security, folio) reconciliation units
    tax_ready_count: int
    needs_attention_count: int
    snapshot_count: int
    # Freshest NAV date backing the priced total + whether that's behind the last
    # trading day (a feed that hasn't run for days). null when nothing is priced yet.
    navs_as_of: date | None = None
    navs_stale: bool = False
    rows: list[InvestorRowOut] = Field(default_factory=list)


class FamilyAggregateOut(Schema):
    family_id: int
    as_of: date
    investor_count: int
    folio_count: int = 0  # distinct folios across the family's investors
    total_inr: Decimal
    asset_mix: list[AssetMixRow]
    top_holdings: list[HoldingValueRow]
    stale_count: int
    integrity_unit_count: int = 0  # (security, folio) reconciliation units
    tax_ready_count: int = 0
    needs_attention_count: int = 0  # mismatches awaiting resolution
    navs_as_of: date | None = None  # freshest NAV date backing the priced total
    navs_stale: bool = False  # the feed hasn't run for >1 trading day
    day_change_inr: Decimal | None = None  # portfolio-wide intraday change (INR)
    # portfolio lifetime money-weighted return as a fraction (0.1849 = 18.49%),
    # over all cashflows incl. sold-out positions. Per-fund XIRR is on each holding.
    xirr: float | None = None


class InvestorSummaryOut(Schema):
    """Per-investor headline numbers for the roster row."""

    investor_id: int
    as_of: date
    total_inr: Decimal
    # True when total_inr is the last *known* value (statement close or last
    # computed day) rather than a live-NAV valuation at as_of — e.g. NAVs not
    # fetched yet. The UI labels it and `as_of` is that value's own date.
    is_provisional: bool = False
    # Freshest NAV date backing the priced total + whether that's stale (the feed
    # hasn't run for >1 trading day). null when nothing is priced yet.
    navs_as_of: date | None = None
    navs_stale: bool = False
    holdings_count: int  # securities currently held (units > 0)
    # (security, folio) reconciliation units — the per-folio integrity unit and the
    # denominator for the tax-ready fraction (a fund in two folios = two units).
    integrity_unit_count: int
    tax_ready_count: int  # of the integrity units, verified for the tax export
    needs_attention_count: int  # mismatches awaiting resolution
    snapshot_count: int  # snapshot-only (no transaction history)
    stale_count: int  # held but unpriced (no NAV on/before as_of)
    # Held mutual funds with no NAV — the fixable subset of stale that the total
    # silently excludes. Excludes equity/bond snapshots (unpriced by design in v1).
    unpriced_fund_count: int
    last_import_at: datetime | None
    day_change_inr: Decimal | None = None  # portfolio-wide intraday change (INR)
    # portfolio lifetime money-weighted return as a fraction (0.1849 = 18.49%),
    # over all cashflows incl. sold-out positions. Per-fund XIRR is on each holding.
    xirr: float | None = None
    asset_mix: list[AssetMixRow] = Field(default_factory=list)  # INR by security type
    # Sub-breakdowns of the priced value so the allocation donut is informative
    # while everything is still mutual funds (value-desc, unpriced rows excluded).
    amc_mix: list[AllocationBucket] = Field(default_factory=list)  # INR by fund house
    category_mix: list[AllocationBucket] = Field(default_factory=list)  # INR by equity/debt
    top_holdings: list[HoldingValueRow] = Field(default_factory=list)  # largest 10, value-desc
    holdings: list[HoldingValueRow] = Field(default_factory=list)  # all priced, value-desc


class ValueSeriesPoint(Schema):
    """One sampled date in the net-worth time series."""

    date: date
    value_inr: Decimal  # held units priced at the latest NAV on/before the date
    invested_inr: Decimal  # FIFO cost basis of the units still held (>= 0)
    stale: bool  # at least one held security had no price on/before the date


class ValuationStatusOut(Schema):
    """Day-wise valuation readiness for an investor or family. The dashboard gates
    the net-worth chart on ``status == 'ready'`` and polls while it isn't; the
    headline numbers stay ungated (backed by the provisional value meanwhile)."""

    investor_id: int | None = None
    family_id: int | None = None
    status: str  # pending | computing | ready | error
    computed_through: date | None = None
    recompute_from: date | None = None
    is_provisional: bool = False  # latest value is the statement's, not yet recomputed


class ValueSeriesOut(Schema):
    """Reconstructed net-worth-over-time series for an investor or family."""

    investor_id: int | None = None
    family_id: int | None = None
    start: date
    end: date
    granularity: str
    points: list[ValueSeriesPoint]


class CasPreviewOut(Schema):
    """Owner identity parsed from an uploaded CAS, before anything is persisted.

    Lets the UI confirm who the statement belongs to (and whether it matches an
    existing investor) before the import creates or attaches. Only a *masked* PAN
    is returned — the full PAN is never sent back to the client.
    """

    kind: str  # "mf_cas" | "ecas"
    investor_name: str
    investor_email: str
    pan_masked: str
    match_investor_id: int | None = None  # set when the PAN matches an existing investor
    match_investor_name: str | None = None
    # Content preview (so a Summary/partial CAS is caught before importing):
    from_date: date | None = None  # statement period (MF CAS)
    to_date: date | None = None  # statement period / eCAS statement date
    scheme_count: int = 0  # MF schemes, or eCAS demat accounts (real ones only)
    transaction_count: int = 0  # MF transaction rows (0 for eCAS)
    holding_count: int = 0  # eCAS holdings (0 for MF CAS)
    # RTA folios in the eCAS "Mutual Fund Folios" section — listed alongside
    # demat accounts in the statement but not one of them (0 for MF CAS).
    mf_folio_count: int = 0
    # True only for a Detailed + since-inception MF CAS with no snapshot-only
    # schemes — i.e. it can build a full cost-basis ledger. eCAS is always False.
    full_history: bool = False
    # MF schemes that'd land as net-worth-only (no transactions, or a non-zero
    # opening with no earlier history) — the "re-download a complete CAS" signal.
    snapshot_scheme_count: int = 0
    # MF schemes dropped because they carry no ISIN/AMFI code (matured/closed/
    # segregated/unclaimed lines) — skipped rather than failing the whole import.
    skipped_unidentified: int = 0


class ImportJobOut(Schema):
    id: int
    investor_id: int
    kind: str
    status: str
    filename: str
    source_ref: str
    result: dict
    error: str
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class ImportJobSummaryOut(Schema):
    """A recent import job for the advisor-wide Settings activity list."""

    id: int
    investor_id: int
    investor_name: str
    kind: str
    status: str
    filename: str
    error: str
    result: dict  # summary counts (schemes/transactions/holdings, reconcile_errors, …)
    created_at: datetime
    finished_at: datetime | None


class ImportQuarantineOut(Schema):
    """An import row/block that couldn't be persisted, set aside for review."""

    id: int
    kind: str
    security_name: str
    isin: str
    folio_number: str
    reason: str
    created_at: datetime


class ValuationIssueOut(Schema):
    """The real, per-security cause behind a generic valuation error."""

    security_id: int
    security_name: str
    identifier: str  # ISIN / AMFI code / symbol — whatever the security is keyed by
    cause: str  # "closed" | "unmapped" | "feed_pending"
    detail: str  # human-readable, actionable explanation


class ValuationDiagnosticsOut(Schema):
    """One investor's valuation status + the actionable cause of any failure."""

    investor_id: int
    investor_name: str
    status: str  # pending | computing | ready | error
    computed_through: date | None = None
    error: str = ""
    attempts: int = 0
    next_attempt_at: datetime | None = None
    issues: list[ValuationIssueOut] = Field(default_factory=list)


class JobsOverviewOut(Schema):
    """Settings 'Jobs & valuation' panel: recent imports + per-investor valuation."""

    imports: list[ImportJobSummaryOut]
    valuations: list[ValuationDiagnosticsOut]


class NavSecurityFreshnessOut(Schema):
    """One tracked security's NAV currency for the freshness panel."""

    security_id: int
    name: str
    security_type: str
    identifier: str  # symbol / ISIN / AMFI code — display identity
    feed_code: str  # what the refresh queries; "" = nothing to query
    latest_nav_date: date | None = None
    first_nav_date: date | None = None
    points: int = 0  # stored history depth
    lag_trading_days: int | None = None  # behind the last trading day; None = unpriced
    status: str  # fresh | grace | stale | pending | closed | no_feed


class NavFreshnessOut(Schema):
    """Settings 'NAV freshness' panel: per-security currency + refresh schedule.

    Read-only — refreshes run on the scheduler (or ``manage.py refresh_navs``);
    the panel shows when prices were last written and when the next pass runs.
    """

    as_of: date  # the last *completed* trading day lags are measured against
    securities: list[NavSecurityFreshnessOut]
    last_refreshed_at: datetime | None = None  # newest NAVHistory write (any source)
    next_refresh_at: datetime  # next scheduled re-fetch pass (absolute instant)


class TransactionIn(Schema):
    """A single manually-entered transaction (security identified inline)."""

    # Security identification (upserted on the fly).
    security_type: str
    name: str
    symbol: str = ""
    isin: str = ""
    amfi_code: str = ""
    coin_id: str = ""
    principal: str = ""
    # Folio the security is held under — required (no folio-less entries). For
    # equity/demat this is the demat account number (matches eCAS exactly) and a
    # broker is required; for MF it's the AMC folio number.
    folio_number: str
    broker: str = ""
    # Transaction fields.
    date: date
    transaction_type: str
    units: Decimal
    price: Decimal
    amount: Decimal | None = None
    fees: Decimal = Decimal("0")
    stamp_duty: Decimal = Decimal("0")
    brokerage: Decimal = Decimal("0")
    currency: str = "INR"
    narration: str = ""  # optional free-text note for a manual entry


class TransactionOut(Schema):
    id: int
    investor_id: int
    security_id: int
    folio_id: int | None
    date: date
    transaction_type: str
    units: Decimal
    nav_or_price: Decimal
    amount: Decimal | None
    fees: Decimal
    stamp_duty: Decimal
    brokerage: Decimal
    currency: str
    source: str
    narration: str  # verbatim source-statement narration (audit trail)
    # False for a partial-history row: shown for context but excluded from cost
    # basis / units / gains. The scheme page badges these.
    cost_basis_complete: bool = True
    # Pre-merger scrip a rebased lot originally traded under (e.g. "HDFC" for an
    # HDFCBANK lot that arrived through the merger); null for natively-held rows.
    via_security: str | None = None
    # Running unit balance after this row (server-computed over the as-traded ledger +
    # corporate-action events); null for an incomplete row that can't carry one.
    balance: Decimal | None = None


class SecurityRef(Schema):
    id: int
    name: str
    isin: str
    symbol: str
    security_type: str
    # When this equity's corporate actions were last fetched (null = never). The
    # integrity UI uses it to gate manual authoring behind "the feed has been
    # checked", so the user doesn't guess an action before the real history lands.
    corporate_actions_synced_at: datetime | None = None


class FolioRef(Schema):
    id: int
    number: str
    broker: str
    folio_type: str


class IntegrityStatusOut(Schema):
    security: SecurityRef
    folio: FolioRef
    status: str
    tax_safe: bool
    units_from_transactions: Decimal | None
    units_from_holdings: Decimal | None
    issues: list[dict]
    ledger_through: date | None
    snapshot_as_of: date | None
    last_reconciled_at: datetime | None


class ApplyCorporateActionIn(Schema):
    """Apply the cached corporate-action references that close a unit gap.

    Usually one event, but a gap can need several applied together (e.g. two splits).
    """

    reference_ids: list[int]


class ApplyCorporateActionOut(Schema):
    updated: int
    created: int
    events_applied: int
    integrity: IntegrityStatusOut


class ManualCorporateActionIn(Schema):
    """Author one corporate action by hand to resolve a flagged unit mismatch.

    The path's security is the affected stock; ``kind`` selects which params apply:
      - ``bonus`` / ``split`` → ``unit_multiplier`` (split < 1 = reverse split)
      - ``merger`` → ``counterparty_*`` (acquirer) + ``merger_ratio`` (new per old)
      - ``rights`` / ``buyback`` → ``units`` + ``price``
    """

    kind: str
    ex_date: date
    unit_multiplier: Decimal | None = None
    merger_ratio: Decimal | None = None
    units: Decimal | None = None
    price: Decimal | None = None
    counterparty_isin: str = ""
    counterparty_symbol: str = ""
    counterparty_name: str = ""


class RecordOpeningLotIn(Schema):
    """Classified opening lot for an eCAS-only equity holding."""

    classification: str  # ipo_allotment | transfer_in | demerger_result
    date: date
    units: Decimal | None = None
    price: Decimal | None = None
    cost_basis_unknown: bool = False


class RecordOpeningLotOut(Schema):
    created: int
    classification: str
    units: str
    cost_basis_complete: bool
    integrity: IntegrityStatusOut


class OpeningLotRow(Schema):
    """One acquisition lot — a row from the broker's per-lot demerger breakdown."""

    date: date
    units: Decimal
    price: Decimal | None = None


class RecordOpeningLotsIn(Schema):
    """Several opening lots at once — a demerger receipt's per-lot allocation
    (date, qty, allocated cost), transcribed from the broker's holding breakdown."""

    classification: str  # ipo_allotment | transfer_in | demerger_result
    lots: list[OpeningLotRow]
    cost_basis_unknown: bool = False
    # The demerger's ex-date — links the receipt to its parent and reduces the parent's
    # cost basis at that date. Required to link; without it the lots are recorded unlinked.
    demerger_date: date | None = None


class RemoveOpeningLotOut(Schema):
    removed: int
    # The (security, folio) status after removal — absent if the row no longer survives.
    integrity: IntegrityStatusOut | None = None


class SuggestedParent(Schema):
    """The parent a demerger child was fingerprint-matched to (for the user to confirm)."""

    id: int
    name: str
    isin: str


class RecordOpeningLotsOut(Schema):
    created: int
    net_units: str
    # A fully-sold child reconciles to net 0 and carries no surviving status row.
    integrity: IntegrityStatusOut | None = None
    # Set when a demerger receipt was linked to a parent whose cost basis was reduced.
    suggested_parent: SuggestedParent | None = None


class IdentityRemapIn(Schema):
    """Re-point ledger rows from the current security to another ISIN."""

    to_isin: str
    to_symbol: str = ""
    to_name: str = ""


class IdentityRemapOut(Schema):
    transactions_updated: int
    holdings_updated: int
    target_security_id: int
    target_isin: str
    integrity: IntegrityStatusOut


class SchemeRef(Schema):
    """Security identity for the scheme-detail header band."""

    id: int
    name: str
    isin: str
    symbol: str
    security_type: str
    amfi_code: str
    amc: str | None = None  # AMC name, when known
    category: str | None = None  # fund category from metadata, when known


class NavPoint(Schema):
    date: date
    nav: Decimal


class FolioBalanceOut(Schema):
    """Final balance for one folio holding a security (units + value at latest NAV)."""

    number: str
    broker: str
    folio_type: str
    units: Decimal
    value_inr: Decimal | None


class DividendTimelineRow(Schema):
    """One dividend event on the equity scheme-detail timeline."""

    reference_id: int
    ex_date: date
    record_date: date | None = None
    dividend_per_share: Decimal
    units: Decimal | None = None
    amount_inr: Decimal | None = None
    # schedule = feed only; attributed = on ledger; computed = ledger-eligible but
    # not yet written; estimate = snapshot-only forward on current units.
    kind: str


class SchemeCorporateActionRow(Schema):
    """One applied issuer event on the scheme-detail corporate-actions timeline."""

    ex_date: date
    kind: str  # bonus | split | merger
    ratio: str  # human ratio, e.g. "1:1", "1:2", "1.68 new per old"
    counterparty: str | None = None  # merged-away / acquiring scrip, for a merger
    units_added: Decimal | None = None  # bonus shares issued (null for unit-neutral events)
    units_after: Decimal  # running balance once the event has acted


class SchemeDetailOut(Schema):
    """Everything one scheme page needs in a single call: identity, current
    metrics, integrity per folio, the NAV history series, and the ledger."""

    security: SchemeRef
    as_of: date
    units: Decimal
    value_inr: Decimal | None
    invested_inr: Decimal | None  # FIFO cost basis of units still held (>= 0)
    return_pct: float | None  # (value - invested) / invested, as a fraction
    xirr: float | None  # money-weighted annualized return of this fund alone
    # Why the XIRR reads the way it does: "valid", "less_than_1_year" (annualized
    # over a short period — indicative), or "estimated" (snapshot-only / unpriced,
    # no real cashflow-based rate).
    xirr_status: str
    day_change_inr: Decimal | None
    day_change_pct: float | None
    latest_nav: Decimal | None
    latest_nav_date: date | None
    has_transactions: bool  # false → snapshot-only (show the no-history banner)
    # True when some ledger rows are partial-history (kept for display, no cost
    # basis); the UI badges those rows and shows a "history before … is missing"
    # banner. ``partial_history_from`` is the earliest such row's date.
    partial_history: bool = False
    partial_history_from: date | None = None
    integrity: list[IntegrityStatusOut]  # one per folio the security is held in
    # Final balance per folio holding this security (units + value). Empty/one-row
    # for a single-folio holding; the UI shows it only when held across >1 folio.
    folios: list[FolioBalanceOut]
    nav_history: list[NavPoint]
    # Equity dividend schedule; empty for mutual funds.
    dividends: list[DividendTimelineRow] = Field(default_factory=list)
    dividends_received_inr: Decimal | None = None
    dividend_yield_on_cost: float | None = None
    # Applied issuer events (bonus / split / merger) with their ratio and running
    # balance — how the holding evolved, distinct from the trade ledger below.
    corporate_actions: list[SchemeCorporateActionRow] = Field(default_factory=list)
    transactions: list[TransactionOut]


# Goes out with every capital-gains worksheet so nobody mistakes it for a filed,
# professionally-checked return. The worksheet's free — this is about posture, not
# price: it's a draft you take to your tax professional, never a tax filing.
TAX_WORKSHEET_DISCLAIMER = (
    "Quick heads up: This is just a draft to help you and your tax pro get started. "
    "It's not tax advice or a filing. The numbers are based on what you've imported; "
    "they might be off if something was missing or misread. Always double-check before you file! "
    "No warranty—use at your own risk."
)


class Schedule112ARequest(Schema):
    fy: str = Field(pattern=r"^\d{4}-\d{2}$", description="India FY, e.g. 2024-25")
    include_unreconciled: bool = False


class CapitalGainRow(Schema):
    """One realised disposal (FIFO lot) in the capital-gains view."""

    security_id: int | None  # Django id for deep-linking; None if not resolvable
    name: str
    isin: str
    units: Decimal
    sale_value: Decimal  # gross consideration (units * sale price)
    cost: Decimal  # cost of acquisition (FMV-grandfathered where applicable)
    gain: Decimal
    term: str  # "short" | "long"
    acquired_on: date
    sold_on: date
    # Pre-2018 LTCG lot whose 31-Jan-2018 FMV is missing — gain may be overstated.
    grandfathering_unavailable: bool = False


class CapitalGainsOut(Schema):
    """Realised capital gains for one FY — STCG/LTCG split for listed equity and
    equity-oriented mutual funds."""

    fy: str
    stcg_total: Decimal
    ltcg_total: Decimal
    rows: list[CapitalGainRow] = Field(default_factory=list)
    disclaimer: str = TAX_WORKSHEET_DISCLAIMER


class Schedule112AResponse(Schema):
    fy: str
    include_unreconciled: bool
    row_count: int
    columns: list[str]
    rows: list[dict[str, str]]
    # Call it what it is — a draft worksheet to review, not a tax filing. Defaulted
    # so every response carries them no matter who builds it.
    title: str = "Capital-gains worksheet (for review)"
    is_draft: bool = True
    disclaimer: str = TAX_WORKSHEET_DISCLAIMER
