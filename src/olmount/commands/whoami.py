import click
from olmount.config import Config

@click.command()
@click.option("--server")
def whoami_cmd(server):
    cfg = Config.load()
    name = server or cfg.default_server()
    prof = cfg.server(name)
    click.echo(f"{prof.email} (id={prof.user_id}) @ {prof.url}")
