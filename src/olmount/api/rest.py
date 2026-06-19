from __future__ import annotations
import io, re, zipfile, json
from olmount.api.http_client import HttpClient

_META_RE = re.compile(r'<meta\s+name="ol-prefetchedProjectsBlob"\s+content=(?P<q>["\'])(?P<c>.*?)(?P=q)')
_PROJECTS_RE = re.compile(r'<meta\s+name="ol-projects"\s+content=(?P<q>["\'])(?P<c>.*?)(?P=q)')

class OverleafREST:
    def __init__(self, http: HttpClient): self.http = http

    def list_projects(self) -> list[dict]:
        r = self.http.get("project")
        for rx in (_META_RE, _PROJECTS_RE):
            m = rx.search(r.text)
            if m:
                data = json.loads(m["c"].replace("&quot;", '"'))
                if isinstance(data, list):
                    return data
                return data.get("projects", [])
        return []

    def download_zip(self, project_id: str) -> zipfile.ZipFile:
        r = self.http.get(f"project/{project_id}/download/zip", stream=True)
        if r.status_code != 200: raise RuntimeError(f"zip download failed: {r.status_code}")
        return zipfile.ZipFile(io.BytesIO(r.content))

    def get_file(self, project_id: str, file_id: str) -> bytes:
        r = self.http.get(f"project/{project_id}/file/{file_id}", stream=True)
        if r.status_code != 200: raise RuntimeError(f"file download failed: {r.status_code}")
        return r.content

    # ---- structural writes (used by engine in M8) ----
    def add_doc(self, project_id, parent_folder_id, name) -> dict:
        r = self.http.post_json(f"project/{project_id}/doc",
                                {"parent_folder_id": parent_folder_id, "name": name},
                                {"X-Csrf-Token": self.http.csrf})
        return r.json()

    def add_folder(self, project_id, parent_folder_id, name) -> dict:
        r = self.http.post_json(f"project/{project_id}/folder",
                                {"name": name, "parent_folder_id": parent_folder_id},
                                {"X-Csrf-Token": self.http.csrf})
        return r.json()

    def upload_file(self, project_id, parent_folder_id, name, data: bytes) -> dict:
        r = self.http.post_multipart(
            f"project/{project_id}/upload",
            data={"folder_id": parent_folder_id, "_csrf": self.http.csrf, "qqfilename": name},
            files={"qqfile": (name, data)})
        return r.json()

    def delete_entity(self, project_id, kind, entity_id):
        self.http.delete(f"project/{project_id}/{kind}/{entity_id}")

    def rename_entity(self, project_id, kind, entity_id, name):
        self.http.post_json(f"project/{project_id}/{kind}/{entity_id}/rename",
                            {"name": name}, {"X-Csrf-Token": self.http.csrf})

    def move_entity(self, project_id, kind, entity_id, folder_id):
        self.http.post_json(f"project/{project_id}/{kind}/{entity_id}/move",
                            {"folder_id": folder_id}, {"X-Csrf-Token": self.http.csrf})

    # ---- compile (wired in M11; CDN download finalized there) ----
    def compile(self, project_id, root_resource_path=None, draft=False, stop_on_first_error=False) -> dict:
        body = {"check": "silent", "draft": draft, "incrementalCompilesEnabled": True,
                "rootResourcePath": root_resource_path, "stopOnFirstError": stop_on_first_error}
        r = self.http.post_json(f"project/{project_id}/compile?auto_compile=true", body,
                                {"X-Csrf-Token": self.http.csrf})
        return r.json()

    def download_output(self, project_id, output_file, compile_group,
                        clsi_server_id=None, pdf_download_domain=None) -> bytes:
        url = output_file["url"]
        if pdf_download_domain and clsi_server_id:
            cdn = (f"{pdf_download_domain.rstrip('/')}/{url.lstrip('/')}"
                   f"?compileGroup={compile_group}"
                   f"&clsiserverid={clsi_server_id}"
                   f"&enable_pdf_caching=true")
            # CDN is cross-origin: do NOT send web cookies
            return self.http.http_get_absolute(cdn, include_cookies=False).content
        # legacy: download via the web frontend (cookies required)
        return self.http.get(url.lstrip("/")).content
