import click
from olmount.commands._run import build_engine

@click.command()
@click.option("--force", is_flag=True)
def pull_cmd(force):
    eng, sock = build_engine()
    try:
        r = eng.reconcile(direction="pull")
        _report(r)
    finally:
        sock.disconnect()

def _report(r):
    for k in ("pulled", "pushed", "deleted", "conflicts"):
        for p in r.get(k, []):
            click.echo(f"{k}: {p}")
