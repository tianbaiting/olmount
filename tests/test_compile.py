import responses
from olmount.api.http_client import HttpClient
from olmount.api.rest import OverleafREST

@responses.activate
def test_compile_then_download_pdf_via_cdn():
    compile_resp = {"status": "success", "compileGroup": "g", "clsiServerId": "srv",
                    "pdfDownloadDomain": "https://cdn.lab.edu",
                    "outputFiles": [{"path": "output.pdf", "type": "pdf",
                                     "url": "/project/p1/user/u/build/b1/output/output.pdf"}]}
    responses.add(responses.POST, "https://ol.lab.edu/project/p1/compile", json=compile_resp, status=200)
    responses.add(responses.GET,
                  "https://cdn.lab.edu/project/p1/user/u/build/b1/output/output.pdf",
                  body=b"%PDF-1.4", status=200)
    rest = OverleafREST(HttpClient("https://ol.lab.edu/", "c", "csrf"))
    res = rest.compile("p1", root_resource_path="main.tex")
    assert res["status"] == "success"
    pdf = rest.download_output("p1", res["outputFiles"][0], res.get("compileGroup"),
                               res.get("clsiServerId"), res.get("pdfDownloadDomain"))
    assert pdf.startswith(b"%PDF")
    # CDN request must NOT carry the web cookie (cross-origin)
    cdn_req = responses.calls[-1].request
    assert "Cookie" not in {k.title() for k in cdn_req.headers.keys()}

@responses.activate
def test_download_output_legacy_via_web_when_no_cdn():
    # no pdfDownloadDomain/clsiServerId -> legacy path through web frontend (cookies sent)
    responses.add(responses.GET, "https://ol.lab.edu/project/p1/user/u/build/b1/output/output.pdf",
                  body=b"%PDF-legacy", status=200)
    rest = OverleafREST(HttpClient("https://ol.lab.edu/", "c", "csrf"))
    of = {"path": "output.pdf", "type": "pdf", "url": "/project/p1/user/u/build/b1/output/output.pdf"}
    pdf = rest.download_output("p1", of, "g", None, None)
    assert pdf == b"%PDF-legacy"
    # web request SHOULD send the cookie
    assert "c" in responses.calls[0].request.headers["Cookie"]
