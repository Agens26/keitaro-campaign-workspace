"""Small server-side Keitaro Admin API client. The API key never reaches the browser."""
import os
import httpx


class KeitaroError(Exception):
    pass


class Keitaro:
    def __init__(self, url=None, api_key=None, transport=None):
        self.url = (url or os.getenv("KEITARO_URL", "")).rstrip("/")
        self.api_key = api_key or os.getenv("KEITARO_API_KEY", "")
        self.transport = transport

    def request(self, method, path, **kwargs):
        if not self.url or not self.api_key:
            raise KeitaroError("Configure KEITARO_URL and KEITARO_API_KEY in .env first.")
        try:
            with httpx.Client(base_url=self.url + "/admin_api/v1", headers={"Api-Key": self.api_key}, timeout=15, transport=self.transport) as client:
                response = client.request(method, path, **kwargs)
                response.raise_for_status()
                return response.json() if response.content else None
        except httpx.HTTPStatusError as exc:
            # Do not disclose credentials or raw response bodies to the UI.
            raise KeitaroError(f"Keitaro returned HTTP {exc.response.status_code} for {method} {path}.") from exc
        except (httpx.RequestError, ValueError) as exc:
            raise KeitaroError(f"Keitaro request failed for {method} {path}. Check connectivity and configuration.") from exc

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, data):
        return self.request("POST", path, json=data)

    def put(self, path, data):
        return self.request("PUT", path, json=data)
