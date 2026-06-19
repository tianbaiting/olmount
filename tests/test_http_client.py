import responses, pytest
from olmount.api.http_client import HttpClient

@responses.activate
def test_get_sends_cookie_csrf_and_retries_5xx():
    url = "https://ol.lab.edu/"
    responses.add(responses.GET, url + "project", status=500)
    responses.add(responses.GET, url + "project", status=200, body="OK")
    c = HttpClient(base_url=url, cookie="sharelatex.sid=x", csrf="csrf")
    r = c.get("project")
    assert r.status_code == 200
    assert r.text == "OK"
    assert "sharelatex.sid=x" in responses.calls[0].request.headers["Cookie"]

@responses.activate
def test_get_raises_on_401_no_retry():
    responses.add(responses.GET, "https://ol.lab.edu/login", status=401)
    c = HttpClient("https://ol.lab.edu/", "c", "csrf")
    with pytest.raises(Exception): c.get("login")
    assert len(responses.calls) == 1
