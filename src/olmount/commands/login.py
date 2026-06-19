import click
from olmount.config import Config
from olmount.api.auth import cookie_login, password_login, CookieExpired

@click.command()
@click.option("--server")
@click.option("--cookie")
@click.option("--user")
def login_cmd(server, cookie, user):
    cfg = Config.load()
    name = server or cfg.default_server()
    if not name:
        raise click.UsageError("no server; run `olmount servers add NAME --url URL` first")
    prof = cfg.server(name)
    if cookie:
        info = cookie_login(prof.url, cookie)
        cfg.set_server(name, cookie=cookie, csrf=info.csrf, user_id=info.user_id, email=info.email)
    elif user:
        pw = click.prompt("password", hide_input=True)
        ck, csrf = password_login(prof.url, user, pw)
        info = cookie_login(prof.url, ck)
        cfg.set_server(name, cookie=ck, csrf=csrf, user_id=info.user_id, email=info.email)
    else:
        raise click.UsageError("provide --cookie or --user")
    cfg.save()
    click.echo(f"logged in as {info.email} on '{name}'")
