import djclick as click

from folioman.utils import update_portfolio_value


@click.command()
def compute_values():
    update_portfolio_value()
