import click
from olmount.commands._run import build_engine
from olmount.commands.pull import _report

@click.command()
def sync_cmd():
    eng, sock = build_engine()
    try:
        _report(eng.reconcile(direction="both"))
    finally:
        sock.disconnect()
