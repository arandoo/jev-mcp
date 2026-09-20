"""Thin async client for TypeSafe AI's System One (Jev) endpoint.

API contract: POST {base}/v1/systemone, base = https://jev-ai.pro/api
  Authorization: Bearer <JEV_AI_API_KEY>
  body: { "state": str|object|array, "model": str, "questions": {id: Question} }
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx

DEFAULT_BASE = "https://jev-ai.pro/api"
DEFAULT_MODEL = "jev-latest"
RETRY_STATUSES = {429, 529}


class JevError(RuntimeError):
    """Raised with an actionable message when the API call fails."""


class JevClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        self.api_key = api_key or os.environ.get("JEV_AI_API_KEY")
        if not self.api_key:
            raise JevError(
                "JEV_AI_API_KEY is not set. Export it or add it to .env "
                "(see .env.example)."
            )
        self.base_url = (base_url or os.environ.get("JEV_AI_API_BASE") or DEFAULT_BASE).rstrip("/")
        self.default_model = os.environ.get("JEV_DEFAULT_MODEL", DEFAULT_MODEL)
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )
        self.max_retries = max_retries

    async def evaluate(
        self,
        state: str | dict[str, Any] | list[Any],
        questions: dict[str, dict[str, Any]],
        model: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "state": state,
            "model": model or self.default_model,
            "questions": questions,
        }
        delay = 1.0
        for attempt in range(self.max_retries + 1):
            resp = await self._http.post("/v1/systemone", json=payload)
            if resp.status_code in RETRY_STATUSES and attempt < self.max_retries:
                await asyncio.sleep(delay)
                delay *= 2
                continue
            if resp.status_code == 401:
                raise JevError("401 Unauthorized: JEV_AI_API_KEY is missing or invalid.")
            if resp.status_code == 422:
                raise JevError(f"422 validation error from TypeSafe: {resp.text}")
            if resp.is_error:
                raise JevError(f"TypeSafe API error {resp.status_code}: {resp.text}")
            return resp.json()
        raise JevError("TypeSafe API kept returning 429/529 after retries; try again later.")

    async def aclose(self) -> None:
        await self._http.aclose()
