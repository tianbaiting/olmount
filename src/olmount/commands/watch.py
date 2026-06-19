import click
from olmount.commands._run import build_engine
from olmount.sync.watcher import Watcher

@click.command()
@click.option("--interval", default=5, type=int)
@click.option("--debounce", default=1.0, type=float)
def watch_cmd(interval, debounce):
    eng, sock = build_engine()
    def reconcile():
        try:
            r = eng.reconcile(direction="both")
            click.echo("synced")
        except Exception as e:
            click.echo(f"sync error: {e}")
    w = Watcher(eng.working_root, interval, debounce, reconcile)
    click.echo("watching (Ctrl-C to stop)")
    try:
        w.run()
    finally:
        sock.disconnect()
