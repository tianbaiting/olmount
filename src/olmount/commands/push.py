import click
from olmount.commands._run import build_engine
from olmount.commands.pull import _report

@click.command()
@click.option("--force", is_flag=True)
def push_cmd(force):
    eng, sock = build_engine()
    try:
        _report(eng.reconcile(direction="push"))
    finally:
        sock.disconnect()
