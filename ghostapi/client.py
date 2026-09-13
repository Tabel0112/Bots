"""Bounded HTTP client shared by the DOM and visual workers."""

import os
from urllib.parse import quote

import httpx


class GhostError(Exception):
    def __init__(self, code, message, status=None):
        self.code, self.status = code, status
        super().__init__(message)


class GhostClient:
    def __init__(self, base_url=None, *, transport=None, timeout=5.0):
        self.http = httpx.AsyncClient(
            base_url=(
                base_url or os.environ.get("GHOST_API_URL", "http://127.0.0.1:8765")
            ).rstrip("/"),
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def close(self):
        await self.http.aclose()

    async def request(self, method, path, body=None):
        try:
            response = await self.http.request(method, path, json=body)
        except httpx.HTTPError:
            raise GhostError(
                "GHOST_UNAVAILABLE", "Ghost request failed or timed out."
            ) from None
        if response.is_error or response.is_redirect:
            raise GhostError(
                "GHOST_HTTP_ERROR",
                f"Ghost returned HTTP {response.status_code}.",
                response.status_code,
            )
        try:
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except ValueError:
            raise GhostError(
                "GHOST_INVALID_RESPONSE", "Ghost returned an invalid response."
            ) from None

    async def lookup(self, body):
        data = await self.request("POST", "/v1/workflows/lookup", body)
        if data.get("decision") not in {"reuse", "explore"}:
            raise GhostError(
                "GHOST_INVALID_RESPONSE", "Ghost omitted its lookup decision."
            )
        if data["decision"] == "reuse":
            workflow = data.get("workflow")
            if not isinstance(workflow, dict) or not isinstance(
                workflow.get("bound_steps"), list
            ):
                raise GhostError(
                    "GHOST_INVALID_RESPONSE", "Ghost omitted reusable steps."
                )
            self.path(workflow.get("skill_id"), workflow.get("version"))
            if not isinstance(workflow.get("compatibility_key"), str) or not isinstance(workflow.get("output_schema_id"), str):
                raise GhostError("GHOST_INVALID_RESPONSE", "Ghost omitted workflow compatibility fields.")
        return data

    async def create_candidate(self, body):
        data = await self.request("POST", "/v1/workflows/candidates", body)
        if (
            not isinstance(data.get("skill_id"), str)
            or type(data.get("version")) is not int
        ):
            raise GhostError(
                "GHOST_INVALID_RESPONSE", "Ghost omitted the candidate identity."
            )
        return data

    @staticmethod
    def path(skill_id, version):
        if not isinstance(skill_id, str) or type(version) is not int or version < 1:
            raise GhostError("GHOST_INVALID_RESPONSE", "Invalid workflow identity.")
        return f"/v1/workflows/{quote(skill_id, safe='')}/versions/{version}"

    async def get_workflow(self, skill_id, version):
        return await self.request("GET", self.path(skill_id, version))

    async def report_run(self, skill_id, version, body):
        data = await self.request("POST", self.path(skill_id, version) + "/runs", body)
        if not isinstance(data.get("run_id"), str):
            raise GhostError("GHOST_INVALID_RESPONSE", "Ghost omitted the run identity.")
        return data

    async def submit_qualification(self, skill_id, version, body):
        return await self.request(
            "POST", self.path(skill_id, version) + "/qualification", body
        )
