import pathlib

from django.db import migrations
from django.core.management import call_command

FIXTURE_DIR = pathlib.Path(__file__).resolve().parent.parent / "fixtures"


# noinspection PyUnusedLocal
def load_data(apps, schema_editor):
    for fixture in FIXTURE_DIR.glob("*.yaml"):
        call_command("loaddata", fixture, verbosity=1)
    call_command("load_schemes")


# noinspection PyUnusedLocal
def unload_data(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("mutualfunds", "0001_initial"),
    ]

    operations = [migrations.RunPython(load_data, unload_data)]
