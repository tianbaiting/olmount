import click, pathlib
from olmount.commands._run import build_engine

@click.command()
@click.option("-o", "out", default="output.pdf")
def pdf_cmd(out):
    eng, sock = build_engine()
    try:
        res = eng.rest.compile(eng.project_id)
        if res.get("status") != "success":
            click.echo(f"compile failed: {res.get('status')}")
            return
        pdf_file = next(f for f in res["outputFiles"] if f["type"] == "pdf")
        data = eng.rest.download_output(eng.project_id, pdf_file, res.get("compileGroup"),
                                        res.get("clsiServerId"), res.get("pdfDownloadDomain"))
        pathlib.Path(out).write_bytes(data)
        click.echo(f"wrote {out}")
    finally:
        sock.disconnect()
