import json
import logging

import casparser
import djclick as click

from mutualfunds.importers.cas import import_cas


@click.command()
@click.option(
    "-p",
    "password",
    metavar="PASSWORD",
    prompt="Enter PDF password",
    hide_input=True,
    confirmation_prompt=False,
    help="CAS password",
)
@click.argument("input_file", type=click.Path(exists=True, dir_okay=False), metavar="CAS_PDF_FILE")
def cas_importer(password, input_file):
    logger = logging.getLogger(__name__)

    logger.info("Reading CAS PDF")
    if input_file.endswith('json'):
        json_data = json.load(open(input_file))
        pdf_data = casparser.CASParserDataType(json_data)
    else:
        pdf_data = casparser.read_cas_pdf(input_file, password,
                                      force_pdfminer=True)
    period = pdf_data["statement_period"]
    email = pdf_data["investor_info"]["email"]
    click.echo("CAS file type " + click.style(pdf_data["file_type"], fg="green", bold=True))
    click.echo(
        "CAS statement period: "
        + click.style(period["from"], fg="green", bold=True)
        + " to "
        + click.style(period["to"], fg="green", bold=True)
    )
    click.echo("Email : " + click.style(email, fg="green", bold=True))
    try:
        result = import_cas(pdf_data, 1)
    except ValueError as e:
        click.style("Error while importing CAS :: %s" % str(e), bold=True, fg="red")
    else:
        click.echo(
            "Total Transactions : "
            + click.style(f"{result['transactions']['total']}", fg="green", bold=True)
        )
        click.echo(
            "Imported : " + click.style(f"{result['transactions']['total']}", fg="green", bold=True)
        )
