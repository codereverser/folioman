import djclick as click

from folioman.utils import update_portfolio_value


@click.command()
@click.option("-p", "--portfolio", type=click.INT, help="Portfolio ID (optional)")
@click.option("-s", "--start-date", type=click.DateTime(formats=["%Y-%m-%d"]))
def compute_values(portfolio, start_date):
    update_portfolio_value(portfolio_id=portfolio, start_date=start_date)
