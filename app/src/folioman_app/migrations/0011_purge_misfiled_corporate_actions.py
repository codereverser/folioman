"""Purge corporate-action rows mis-filed under the wrong security.

BSE's fuzzy symbol search returned a same-prefix *different* company for delisted
symbols (a search for "HDFC" resolves to HDFC BANK), and because BSE rows carry no
ISIN, those events were stamped with the queried security's ISIN — e.g. HDFC Bank's
dividends and 1:1 bonus filed under HDFC Ltd. The fetch now disambiguates by ISIN;
this clears the rows already cached against the wrong security.

A row is mis-filed when its ``symbol`` differs from its own security's symbol. That
test deliberately spares benign ISIN drift (a company's old ISIN carrying its own
current events keeps the same symbol), so only genuine cross-company rows are removed.
"""

from __future__ import annotations

from django.db import migrations


def purge_misfiled(apps, schema_editor):
    CorporateActionReference = apps.get_model("folioman_app", "CorporateActionReference")
    misfiled = [
        row.pk
        for row in CorporateActionReference.objects.select_related("security").exclude(symbol="")
        if row.security and row.symbol.upper() != (row.security.symbol or "").upper()
    ]
    if misfiled:
        CorporateActionReference.objects.filter(pk__in=misfiled).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("folioman_app", "0010_security_corporate_actions_synced_at"),
    ]

    operations = [
        migrations.RunPython(purge_misfiled, migrations.RunPython.noop),
    ]
