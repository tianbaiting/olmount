from __future__ import annotations
import time
import requests

class HttpError(Exception): ...

class HttpClient:
    def __init__(self, base_url: str, cookie: str, csrf: str = "",
                 timeout: int = 30, max_retries: int = 3):
        self.base_url = base_url if base_url.endswith("/") else base_url + "/"
        self.cookie = cookie
        self.csrf = csrf
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()

    def _headers(self, extra: dict | None = None) -> dict:
        h = {"Connection": "keep-alive", "Cookie": self.cookie}
        if extra: h.update(extra)
        return h

    def _retry(self, method, url, **kwargs):
        last = None
        for attempt in range(self.max_retries + 1):
            resp = self.session.request(method, url, timeout=self.timeout,
                                        allow_redirects=False, **kwargs)
            if resp.status_code in (401, 403):
                raise HttpError(f"{method} {url} auth failed: {resp.status_code}")
            if resp.status_code < 500 and resp.status_code != 429:
                return resp
            last = resp
            time.sleep(0.5 * (2 ** attempt))
        raise HttpError(f"{method} {url} failed after retries: {last.status_code if last else '?'}")

    def get(self, route: str, extra_headers: dict | None = None, stream: bool = False):
        return self._retry("GET", self.base_url + route.lstrip("/"),
                           headers=self._headers(extra_headers), stream=stream)

    def post_json(self, route: str, body: dict | None = None, extra_headers: dict | None = None):
        body = dict(body or {})
        body.setdefault("_csrf", self.csrf)
        headers = self._headers(extra_headers); headers["Content-Type"] = "application/json"
        return self._retry("POST", self.base_url + route.lstrip("/"), headers=headers, json=body)

    def post_multipart(self, route: str, data=None, files=None, extra_headers=None):
        return self._retry("POST", self.base_url + route.lstrip("/"),
                           headers=self._headers(extra_headers), data=data, files=files)

    def delete(self, route: str, extra_headers: dict | None = None):
        headers = self._headers(extra_headers or {}); headers["X-Csrf-Token"] = self.csrf
        return self._retry("DELETE", self.base_url + route.lstrip("/"), headers=headers)
