import click
from olmount.commands._run import build_engine

@click.command()
@click.option("--root")
@click.option("--draft", is_flag=True)
@click.option("--stop-on-first-error", is_flag=True)
def compile_cmd(root, draft, stop_on_first_error):
    eng, sock = build_engine()
    try:
        res = eng.rest.compile(eng.project_id, root_resource_path=root, draft=draft,
                               stop_on_first_error=stop_on_first_error)
        click.echo(f"compile: {res.get('status')}")
        errs = (res.get("stats", {}) or {}).get("latexmk-errors", 0)
        if errs:
            click.echo(f"latexmk-errors: {errs}")
    finally:
        sock.disconnect()
