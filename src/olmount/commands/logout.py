import click
from olmount.config import Config

@click.command()
@click.option("--server")
def logout_cmd(server):
    cfg = Config.load()
    name = server or cfg.default_server()
    try:
        cfg.server(name)
    except KeyError:
        raise click.ClickException(f"unknown server '{name}'; run `olmount servers list`")
    cfg.set_server(name, cookie="", csrf="", user_id="", email="")
    cfg.save()
    click.echo(f"cleared credentials for '{name}' (server profile kept)")
