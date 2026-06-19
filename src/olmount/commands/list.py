import click
from olmount.config import Config
from olmount.api.http_client import HttpClient
from olmount.api.rest import OverleafREST

@click.command(name="list")
@click.option("--server")
def list_cmd(server):
    cfg = Config.load()
    prof = cfg.server(server or cfg.default_server())
    rest = OverleafREST(HttpClient(prof.url, prof.cookie, prof.csrf))
    for p in rest.list_projects():
        click.echo(f"{p.get('id')}\t{p.get('name')}")
