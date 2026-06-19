import io, zipfile, responses
from olmount.api.rest import OverleafREST
from olmount.api.http_client import HttpClient

def _client():
    return OverleafREST(HttpClient("https://ol.lab.edu/", "sharelatex.sid=x", "csrf"))

@responses.activate
def test_list_projects_parses_meta():
    html = '<meta name="ol-prefetchedProjectsBlob" content=\'{"totalSize":1,"projects":[{"id":"p1","name":"paper"}]}\'>'
    responses.add(responses.GET, "https://ol.lab.edu/project", status=200, body=html)
    projects = _client().list_projects()
    assert [p["name"] for p in projects] == ["paper"]

@responses.activate
def test_download_zip_returns_zipfile():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z: z.writestr("main.tex", "hello")
    responses.add(responses.GET, "https://ol.lab.edu/project/p1/download/zip",
                  status=200, body=buf.getvalue(), content_type="application/zip")
    zf = _client().download_zip("p1")
    assert zf.read("main.tex") == b"hello"

@responses.activate
def test_get_file_bytes():
    responses.add(responses.GET, "https://ol.lab.edu/project/p1/file/f9",
                  status=200, body=b"\x89PNG")
    assert _client().get_file("p1", "f9") == b"\x89PNG"
