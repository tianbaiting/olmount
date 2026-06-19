import click
from olmount.config import Config

@click.group()
@click.version_option()
def main():
    """olmount -- two-way sync for Overleaf (incl. self-hosted)."""

@main.group()
def servers():
    """Manage server profiles."""

@servers.command("add")
@click.argument("name")
@click.option("--url", required=True)
def servers_add(name, url):
    cfg = Config.load()
    cfg.set_server(name, url=url)
    cfg.save()
    click.echo(f"added server '{name}' -> {url}")

@servers.command("list")
def servers_list():
    cfg = Config.load()
    if not cfg.servers:
        click.echo("(no servers; run `olmount servers add NAME --url URL`)")
        return
    for n, s in cfg.servers.items():
        mark = "*" if n == cfg.default_server() else " "
        click.echo(f"{mark} {n}\t{s.url}")

@servers.command("set-default")
@click.argument("name")
def servers_set_default(name):
    cfg = Config.load()
    cfg.set_default(name)
    cfg.save()

# register remaining commands (implemented in their modules; created in this or later tasks)
from olmount.commands.login import login_cmd; main.add_command(login_cmd)            # noqa: E402
from olmount.commands.logout import logout_cmd; main.add_command(logout_cmd)         # noqa: E402
from olmount.commands.whoami import whoami_cmd; main.add_command(whoami_cmd)         # noqa: E402

if __name__ == "__main__":
    main()
