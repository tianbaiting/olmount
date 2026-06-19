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
def test_list_projects_tolerates_data_type_attr():
    # real Overleaf CE inserts data-type="json" between name= and content=, with &quot;-encoded JSON
    html = '''<meta name="ol-projects" data-type="json" content="[{&quot;id&quot;:&quot;p2&quot;,&quot;name&quot;:&quot;legacy&quot;}]">'''
    responses.add(responses.GET, "https://ol.lab.edu/project", status=200, body=html)
    projects = _client().list_projects()
    assert [p["name"] for p in projects] == ["legacy"]

@responses.activate
def test_list_projects_bare_array_meta():
    # older/self-hosted Overleaf: ol-projects is a bare JSON array of project objects
    html = '<meta name="ol-projects" content=\'[{"id":"p2","name":"legacy"}]\'>'
    responses.add(responses.GET, "https://ol.lab.edu/project", status=200, body=html)
    projects = OverleafREST(HttpClient("https://ol.lab.edu/", "c", "csrf")).list_projects()
    assert [p["name"] for p in projects] == ["legacy"]

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
