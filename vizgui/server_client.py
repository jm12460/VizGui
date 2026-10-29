"""Authenticated, Qt-free client for the VizGui headless server."""

import json
import urllib.error
import urllib.request

from . import config


class ServerConnectionError(RuntimeError):
    """The VizGui server could not be reached."""


class ServerClient:
    def __init__(self, host="127.0.0.1", port=None, token=None, timeout=3):
        web = config.CFG.get("web", {})
        self.host = host
        self.port = int(port if port is not None else web.get("port", 8950))
        self.token = token if token is not None else web.get("token", "")
        self.timeout = timeout

    def _request(self, path, payload=None):
        url = f"http://{self.host}:{self.port}{path}"
        headers = {"X-Viz-Token": self.token}
        data = None

        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")

        request = urllib.request.Request(
            url, data=data, headers=headers
        )

        try:
            with urllib.request.urlopen(
                request, timeout=self.timeout
            ) as response:
                return json.load(response)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise ServerConnectionError(
                f"Cannot communicate with VizGui at "
                f"{self.host}:{self.port}: {exc}"
            ) from exc

    def status(self):
        return self._request("/api/status")

    def command(self, kind, **payload):
        return self._request("/api/cmd", {"kind": kind, **payload})

    def history(self):
        return self._request("/api/history")

    def available(self):
        try:
            return self.status().get("ok") is True
        except ServerConnectionError:
            return False
