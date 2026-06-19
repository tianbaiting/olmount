import click
from olmount.config import Config

@click.command()
@click.option("--server")
def whoami_cmd(server):
    cfg = Config.load()
    name = server or cfg.default_server()
    try:
        prof = cfg.server(name)
    except KeyError:
        raise click.ClickException(f"unknown server '{name}'; run `olmount servers list`")
    click.echo(f"{prof.email} (id={prof.user_id}) @ {prof.url}")
