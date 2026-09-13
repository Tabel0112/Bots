"""ARGUS-owned Steel sessions and screenshot evidence (P3-SESSION).

This manager is deliberately not wired into :class:`DomToolbox` yet.  It owns
only Steel session creation, release, and screenshot capture; the controller
continues to decide when those operations happen.
"""

from __future__ import annotations

import base64
import binascii
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from argus.contracts import ContractError

__all__ = ["SteelSessionManager"]


class SteelSessionManager:
    """Own at most ``max_open`` Steel sessions in this process.

    The limit is process-local, not a distributed Steel account limit.  When no
    client is injected, the synchronous Steel SDK is imported lazily and reads
    ``STEEL_API_KEY`` here.  Importing this module never imports Steel.

    Screenshots use the same API as the visual worker:
    ``sessions.computer(handle, action="take_screenshot")``.  Its base64 PNG is
    saved under ``evidence_dir`` and exposed by :meth:`paths`; no Playwright/CDP
    connection is opened merely to capture evidence.
    """

    def __init__(
        self,
        client: Any = None,
        max_open: int = 4,
        evidence_dir: str | Path | None = None,
    ) -> None:
        if not isinstance(max_open, int) or isinstance(max_open, bool) or max_open < 1:
            raise ValueError("max_open must be a positive integer")
        self.max_open = max_open
        self.evidence_dir = (
            Path(evidence_dir)
            if evidence_dir is not None
            else Path(tempfile.mkdtemp(prefix="argus-steel-evidence-"))
        )
        self._client = client if client is not None else self._default_client()
        self._sessions: dict[str, dict[str, Any]] = {}
        self._paths: dict[str, str] = {}
        self._observation_number = 0
        self._lock = threading.RLock()

    @staticmethod
    def _default_client() -> Any:
        key = os.getenv("STEEL_API_KEY")
        try:
            from steel import Steel
        except ImportError as exc:
            raise ContractError(
                "P3-SESSION requires the steel package and STEEL_API_KEY.",
                code="PRECONDITION_FAILED",
            ) from exc
        if not key:
            raise ContractError(
                "P3-SESSION requires STEEL_API_KEY and the steel package.",
                code="PRECONDITION_FAILED",
            )
        try:
            return Steel(steel_api_key=key, max_retries=0, timeout=15)
        except Exception as exc:  # noqa: BLE001 - never expose SDK or credential text
            raise ContractError(
                f"Steel client creation failed ({type(exc).__name__}); "
                "check STEEL_API_KEY and the steel package.",
                code="PRECONDITION_FAILED",
            ) from None

    def open(self, site_id: str) -> str:
        """Create a caller-ID Steel session and return the canonical session ID."""
        if not isinstance(site_id, str) or not site_id.strip():
            raise ContractError(
                "site_id must be a nonempty string", code="PRECONDITION_FAILED"
            )
        with self._lock:
            if len(self._open_handles()) >= self.max_open:
                raise ContractError(
                    f"per-process Steel session limit ({self.max_open}) reached",
                    code="BUDGET_EXCEEDED",
                )
            requested = str(uuid4())
            try:
                session = self._client.sessions.create(session_id=requested)
            except Exception as exc:  # noqa: BLE001 - provider detail stays private
                raise ContractError(
                    f"Steel session creation failed ({type(exc).__name__})",
                    code="PRECONDITION_FAILED",
                ) from None
            handle = getattr(session, "id", None)
            if not isinstance(handle, str) or not handle:
                raise ContractError(
                    "Steel session creation returned no session id",
                    code="PRECONDITION_FAILED",
                )
            if handle in self._sessions:
                raise ContractError(
                    "Steel returned a duplicate session id",
                    code="PRECONDITION_FAILED",
                )
            self._sessions[handle] = {
                "site_id": site_id.strip(),
                "created_at": time.time(),
                "open": True,
            }
            return handle

    def close(self, handle: str) -> None:
        """Release a known open session; a repeated close is a no-op."""
        with self._lock:
            state = self._sessions.get(handle)
            if state is None:
                raise ContractError(
                    "unknown Steel session handle", code="PRECONDITION_FAILED"
                )
            if not state["open"]:
                return
            try:
                self._client.sessions.release(handle)
            except Exception as exc:  # noqa: BLE001 - provider detail stays private
                raise ContractError(
                    f"Steel session release failed ({type(exc).__name__})",
                    code="PRECONDITION_FAILED",
                ) from None
            state["open"] = False

    def close_all(self) -> dict[str, list[Any]]:
        """Release all open sessions, continuing after failures.

        Returns ``{"released": [handle, ...], "failures": [{"handle",
        "error"}, ...]}``.  Failure entries carry exception class names only.
        A failed release remains open so a caller may retry it.
        """
        with self._lock:
            handles = list(self._open_handles())
        released: list[str] = []
        failures: list[dict[str, str]] = []
        for handle in handles:
            try:
                self.close(handle)
            except Exception as exc:  # noqa: BLE001 - every handle is attempted
                failures.append({"handle": handle, "error": type(exc).__name__})
            else:
                released.append(handle)
        return {"released": released, "failures": failures}

    def observe(self, handle: str) -> str:
        """Capture a PNG screenshot and return its extensionless observation ID."""
        with self._lock:
            state = self._sessions.get(handle)
            if state is None or not state["open"]:
                raise ContractError(
                    "Steel session is unknown or closed",
                    code="PRECONDITION_FAILED",
                )
            try:
                response = self._client.sessions.computer(
                    handle, action="take_screenshot"
                )
                encoded = getattr(response, "base64_image", None)
                if not isinstance(encoded, str) or not encoded:
                    raise ValueError("missing screenshot")
                if encoded.startswith("data:"):
                    encoded = encoded.partition(",")[2]
                image = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError, TypeError, AttributeError) as exc:
                raise ContractError(
                    f"Steel screenshot was invalid ({type(exc).__name__})",
                    code="PRECONDITION_FAILED",
                ) from None
            except Exception as exc:  # noqa: BLE001 - provider detail stays private
                raise ContractError(
                    f"Steel screenshot failed ({type(exc).__name__})",
                    code="PRECONDITION_FAILED",
                ) from None
            self._observation_number += 1
            observation_id = f"observation-{self._observation_number}"
            self.evidence_dir.mkdir(parents=True, exist_ok=True)
            path = self.evidence_dir / f"{observation_id}.png"
            try:
                path.write_bytes(image)
            except OSError as exc:
                raise ContractError(
                    f"Steel screenshot could not be saved ({type(exc).__name__})",
                    code="PRECONDITION_FAILED",
                ) from None
            self._paths[observation_id] = str(path)
            return observation_id

    def paths(self) -> dict[str, str]:
        """Return a copy of ``observation id -> PNG path``."""
        with self._lock:
            return dict(self._paths)

    def _open_handles(self) -> list[str]:
        return [handle for handle, state in self._sessions.items() if state["open"]]
