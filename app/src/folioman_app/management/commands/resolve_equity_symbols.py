"""manage.py resolve_equity_symbols — repair demat holdings imported without a
trading symbol.

eCAS statements identify equities/bonds by ISIN only. Holdings imported before
symbol resolution existed (or while the ISIN database lacked symbols) have an
empty ``symbol`` and therefore no price feed — so they sit unpriced and
contribute nothing to net worth. This command resolves the NSE/BSE trading
symbol for those rows from the ISIN database (the same source the eCAS import
uses), backfills their price history, and queues a valuation recompute for the
affected investors.

It's idempotent and additive: a row that already has a symbol is skipped, and an
ISIN the database can't map to a symbol (a bond, an unlisted/delisted scrip)
stays unpriced — it degrades, it isn't zeroed.

Symbols come from the ISIN database via casparser, so a symbol-bearing build must
be reachable: either the bundled DB once it ships symbols, or a DB pointed at by
``CASPARSER_ISIN_DB``. If nothing resolves, that's the first thing to check.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "Resolve trading symbols for demat equity/bond holdings imported without one, "
        "backfill their price history, and queue a valuation recompute."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be resolved and write nothing.",
        )
        parser.add_argument(
            "--no-backfill",
            action="store_true",
            help="Set symbols only; skip the price-history backfill.",
        )
        parser.add_argument(
            "--no-recompute",
            action="store_true",
            help="Skip queueing the valuation recompute for affected investors.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help=(
                "Re-pull full history even for securities whose latest price is "
                "current — repairs older/interior gaps the freshness skip can't see "
                "(e.g. a prior run that only fetched a short recent window)."
            ),
        )

    def handle(self, *args, **options) -> None:
        from folioman_app.models import Investor, Security
        from folioman_app.tasks.refresh_navs import _QUOTE_TYPES, fill_gaps
        from folioman_app.tasks.valuation_jobs import _earliest_activity, queue_recompute

        dry_run = options["dry_run"]

        # 1. Resolve symbols for any quote-type holding still missing one. Symbol
        #    resolution needs casparser>=1.0.1 (batch_equity_symbols); if an older
        #    casparser is installed it's skipped with a warning — the backfill and
        #    recompute below still run over already-symboled holdings, so the
        #    command stays useful (and is safe to repeat after a throttled feed
        #    recovers).
        try:
            from casparser.parsers._isin import batch_equity_symbols
        except ImportError:
            batch_equity_symbols = None
            self.stdout.write(
                self.style.WARNING(
                    "casparser has no batch_equity_symbols (needs >=1.0.1); skipping symbol "
                    "resolution — backfilling/recomputing already-symboled holdings only."
                )
            )

        unresolved = (
            list(
                Security.objects.filter(security_type__in=sorted(_QUOTE_TYPES), symbol="").exclude(
                    isin=""
                )
            )
            if batch_equity_symbols is not None
            else []
        )
        symbols = batch_equity_symbols(s.isin for s in unresolved) if unresolved else {}
        matched = [(s, symbols[s.isin]) for s in unresolved if s.isin in symbols]
        unmatched = [s for s in unresolved if s.isin not in symbols]
        any_symboled = (
            Security.objects.filter(security_type__in=sorted(_QUOTE_TYPES))
            .exclude(symbol="")
            .exists()
        )

        self.stdout.write(
            f"{len(unresolved)} symbol-less holdings | "
            f"{len(matched)} newly resolved | {len(unmatched)} unmapped (stay unpriced)"
        )
        for sec, (symbol, exchange) in matched:
            self.stdout.write(f"  {sec.isin} -> {symbol}.{exchange or '?'}  ({sec.name[:40]})")
        # Only flag a *database* problem when nothing — old or new — carries a symbol.
        # A residual tail of unmappable instruments (bonds, rights, unlisted scrips)
        # is normal and not a misconfiguration.
        if unresolved and not matched and not any_symboled:
            self.stdout.write(
                self.style.WARNING(
                    "Nothing resolved and no holding has a symbol. Is the ISIN database "
                    "built with symbols? Point CASPARSER_ISIN_DB at one or rebuild it."
                )
            )

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run — no changes written."))
            return

        for sec, (symbol, exchange) in matched:
            sec.symbol = symbol
            if exchange:
                sec.exchange = exchange
            sec.save(update_fields=["symbol", "exchange", "updated_at"])
        if matched:
            self.stdout.write(self.style.SUCCESS(f"Set symbols on {len(matched)} securities."))

        # 2. Backfill price history for every symboled quote-type holding (not just
        #    the ones resolved this run) — re-running after a throttled feed
        #    recovers fills whatever's still missing; current series are skipped.
        symboled = Security.objects.filter(security_type__in=sorted(_QUOTE_TYPES)).exclude(
            symbol=""
        )
        if not symboled.exists():
            self.stdout.write(self.style.WARNING("No symboled quote-type holdings to price."))
            return

        if not options["no_backfill"]:
            summary = fill_gaps(securities=symboled, force=options["force"])
            self.stdout.write(
                self.style.SUCCESS(
                    f"History backfill: {summary['points']} points across "
                    f"{summary['securities']} securities, "
                    f"{summary['skipped']} skipped, {summary['errors']} errors"
                )
            )

        # 3. Queue a recompute for every investor holding a symboled quote-type
        #    security, so the freshly-priced equities flow into their net worth.
        if not options["no_recompute"]:
            symboled_ids = list(symboled.values_list("id", flat=True))
            investors = (
                Investor.objects.filter(holdings__security_id__in=symboled_ids)
                | Investor.objects.filter(transactions__security_id__in=symboled_ids)
            ).distinct()
            queued = 0
            for inv in investors:
                start = _earliest_activity(inv)
                if start is None:
                    continue
                queue_recompute(inv, start)
                queued += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f"Queued recompute for {queued} investor(s). "
                    "Run `manage.py valuation_tick_pending` or let the scheduler process them."
                )
            )
