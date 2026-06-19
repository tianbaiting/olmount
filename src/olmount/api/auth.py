from __future__ import annotations
import re
from dataclasses import dataclass
from olmount.api.http_client import HttpClient, HttpError

class CookieExpired(Exception): ...

_META = re.compile(r'<meta\s+name="ol-(user_id|usersEmail|csrfToken)"\s+content="([^"]*)">')

@dataclass
class LoginInfo:
    user_id: str; email: str; csrf: str

def cookie_login(base_url: str, cookie: str) -> LoginInfo:
    c = HttpClient(base_url, cookie)
    try:
        r = c.get("project")
    except HttpError as e:
        # 401/403 (and exhausted retries) => cookie rejected / expired
        raise CookieExpired(f"cookie rejected (status {e.status_code}); re-run `olmount login --cookie`") from e
    if r.status_code in (301, 302) and "/login" in r.headers.get("Location", ""):
        raise CookieExpired("cookie rejected (redirected to /login); re-run `olmount login --cookie`")
    if r.status_code != 200:
        raise CookieExpired(f"unexpected status {r.status_code}")
    fields = dict(_META.findall(r.text))
    if "user_id" not in fields or "csrfToken" not in fields:
        raise CookieExpired("could not parse user meta; cookie likely expired")
    return LoginInfo(user_id=fields["user_id"], email=fields.get("usersEmail", ""),
                     csrf=fields["csrfToken"])

def password_login(base_url: str, email: str, password: str) -> tuple[str, str]:
    """Returns (cookie, csrf). Only works where no SSO/captcha (typical self-hosted CE)."""
    c = HttpClient(base_url, cookie="")
    r = c.get("login")
    m = re.search(r'<input.*?name="_csrf".*?value="([^"]*)"', r.text)
    if not m: raise CookieExpired("could not get login CSRF")
    csrf = m[1]
    set_cookie = r.headers.get("set-cookie", "")
    sess = set_cookie.split(";")[0]
    try:
        r2 = c.post_json("login", {"_csrf": csrf, "email": email, "password": password},
                         extra_headers={"Cookie": sess})
    except HttpError as e:
        raise CookieExpired(f"password login failed (status {e.status_code}); captcha/SSO? use --cookie") from e
    if r2.status_code != 302 or "/login" in r2.headers.get("Location", ""):
        raise CookieExpired("password login failed (captcha/SSO? use --cookie)")
    sc = r2.headers.get("set-cookie", "").split(";")[0]
    return f"{sess}; {sc}", csrf
