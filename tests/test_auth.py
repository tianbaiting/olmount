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
