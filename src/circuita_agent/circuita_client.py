"""Thin HTTP client for the circuita API (hosted as a service; its code is not in this repo)."""

import json
from typing import Any

import httpx


class CircuitaError(Exception):
    """The circuita API rejected a request. `detail` is the text worth showing to the model."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"circuita API error {status}: {detail}")
        self.status = status
        self.detail = detail


def _detail_text(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:500]
    detail = body.get("detail", body) if isinstance(body, dict) else body
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):  # FastAPI request-validation errors
        parts = []
        for item in detail:
            if isinstance(item, dict):
                where = ".".join(str(p) for p in item.get("loc", []))
                parts.append(f"{where}: {item.get('msg', '')}")
            else:
                parts.append(str(item))
        return "; ".join(parts)
    return json.dumps(detail, ensure_ascii=False)[:500]


class CircuitaClient:
    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 60.0,
    ) -> None:
        headers = {"X-API-Key": api_key} if api_key else {}
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"), headers=headers, transport=transport, timeout=timeout
        )

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise CircuitaError(0, f"could not reach circuita: {exc}") from exc
        if response.status_code >= 400:
            raise CircuitaError(response.status_code, _detail_text(response))
        return response

    def health(self) -> bool:
        return bool(self._request("GET", "/health").json().get("status") == "ok")

    def parts(self) -> dict[str, Any]:
        """Known parts with their real pinouts, plus generic pin templates (GET /parts)."""
        result: dict[str, Any] = self._request("GET", "/parts").json()
        return result

    def apply_synthesis(
        self, components: list[dict[str, Any]], nets: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Compile, validate and save a new circuit (POST /proposals/apply). No LLM involved."""
        body = {"synthesis": {"components": components, "nets": nets}}
        result: dict[str, Any] = self._request("POST", "/proposals/apply", json=body).json()
        return result

    def apply_edit(self, design_id: str, operations: list[dict[str, Any]]) -> dict[str, Any]:
        """Apply edit operations to a saved design (POST /designs/{id}/proposals/apply)."""
        path = f"/designs/{design_id}/proposals/apply"
        result: dict[str, Any] = self._request("POST", path, json={"operations": operations}).json()
        return result

    def get_design(self, design_id: str) -> dict[str, Any]:
        result: dict[str, Any] = self._request("GET", f"/designs/{design_id}").json()
        return result

    def export_kicad(self, design_id: str, allow_unresolved: bool = False) -> str:
        params = {"allow_unresolved": "true"} if allow_unresolved else {}
        return self._request("GET", f"/designs/{design_id}/export/kicad", params=params).text
