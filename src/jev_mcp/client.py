"""Thin async client for TypeSafe AI's System One (Jev) endpoint.

API contract: POST {base}/v1/systemone, base = https://jev-ai.pro/api
  Authorization: Bearer <JEV_AI_API_KEY>
  body: { "state": str|object|array, "model": str, "questions": {id: Question} }
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import httpx

DEFAULT_BASE = "https://jev-ai.pro/api"
DEFAULT_MODEL = "jev-latest"
RETRY_STATUSES = {429, 529}

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def dotenv_values(path: Path = ENV_FILE) -> dict[str, str]:
    """Read KEY=VALUE pairs from the project's .env, if it exists.

    Only a fallback: real environment variables always take precedence. This
    lets the key live in one 0600 file instead of being copied into every
    client's config, since MCP clients pass env vars but never read .env.
    """
    values: dict[str, str] = {}
    try:
        # utf-8-sig strips a BOM: PowerShell's Set-Content -Encoding utf8 writes
        # one, and it would otherwise become part of the first key's name.
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        values[key] = value.strip().strip('"').strip("'")
    return values


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
        env_file = dotenv_values()
        self.api_key = api_key or os.environ.get("JEV_AI_API_KEY") or env_file.get("JEV_AI_API_KEY")
        if not self.api_key:
            raise JevError(
                f"JEV_AI_API_KEY is not set. Export it, pass it in the MCP "
                f"server's env block, or add it to {ENV_FILE} (see .env.example)."
            )
        self.base_url = (
            base_url
            or os.environ.get("JEV_AI_API_BASE")
            or env_file.get("JEV_AI_API_BASE")
            or DEFAULT_BASE
        ).rstrip("/")
        self.default_model = (
            os.environ.get("JEV_DEFAULT_MODEL")
            or env_file.get("JEV_DEFAULT_MODEL")
            or DEFAULT_MODEL
        )
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
