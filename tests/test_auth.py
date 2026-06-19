import responses, pytest, pathlib
from olmount.api.auth import cookie_login, CookieExpired, password_login

FIX = pathlib.Path("tests/fixtures/login_project.html").read_text()

@responses.activate
def test_cookie_login_parses_user_meta():
    responses.add(responses.GET, "https://ol.lab.edu/project", status=200, body=FIX)
    info = cookie_login("https://ol.lab.edu/", "sharelatex.sid=good")
    assert info.user_id == "u123"
    assert info.email == "me@lab.edu"
    assert info.csrf == "csrfTOKEN"

@responses.activate
def test_cookie_login_redirect_to_login_raises():
    responses.add(responses.GET, "https://ol.lab.edu/project", status=302,
                  headers={"Location": "/login"})
    with pytest.raises(CookieExpired):
        cookie_login("https://ol.lab.edu/", "sharelatex.sid=bad")

@responses.activate
def test_cookie_login_401_raises_expired():
    # HttpClient raises HttpError on 401; cookie_login must surface CookieExpired
    responses.add(responses.GET, "https://ol.lab.edu/project", status=401)
    with pytest.raises(CookieExpired):
        cookie_login("https://ol.lab.edu/", "sharelatex.sid=bad")

@responses.activate
def test_password_login_posts_form_encoded_and_returns_cookie_csrf():
    # GET /login returns a CSRF + session cookie
    responses.add(responses.GET, "https://ol.lab.edu/login", status=200,
                  body='<input name="_csrf" value="CSRF123">',
                  headers={"set-cookie": "anon=ANON; Path=/"})
    # POST /login succeeds (302 to /project) and sets the auth cookie
    responses.add(responses.POST, "https://ol.lab.edu/login", status=302,
                  headers={"Location": "/project", "set-cookie": "overleaf_session2=AUTH; Path=/"})
    cookie, csrf = password_login("https://ol.lab.edu/", "me@lab.edu", "pw")
    assert "AUTH" in cookie and csrf == "CSRF123"
    # the POST must be form-encoded, NOT json
    post_req = responses.calls[1].request
    assert post_req.headers.get("Content-Type", "").startswith("application/x-www-form-urlencoded"), \
        "password login must POST form-encoded data, not JSON"
    body = post_req.body
    assert "_csrf=CSRF123" in body and "email=me%40lab.edu" in body and "password=pw" in body
