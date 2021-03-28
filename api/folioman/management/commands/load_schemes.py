import logging

import djclick as click

from folioman.importers.master import import_master_scheme_data
from folioman.importers.fetcher import fetch_bse_star_master_data

BSE_STARMF_SCHEME_MASTER_URL = "https://bsestarmf.in/RptSchemeMaster.aspx"


@click.command()
@click.option(
    "-i",
    "--input_file",
    type=click.Path(exists=True, dir_okay=False),
    help="BSE StarMF Scheme Master Data (optional)",
)
def load_schemes(input_file):
    """Load BSE StarMF master data file into database"""
    logger = logging.getLogger(__name__)

    if input_file is not None:
        with open(input_file, "r") as fp:
            master_data = fp.read()
    else:
        master_data = fetch_bse_star_master_data()

    logger.info("Importing to database")
    total, valid, inserted = import_master_scheme_data(master_csv_data=master_data)
    logger.info("Summary: Total %d :: Valid %d :: Inserted %d", total, valid, inserted)
