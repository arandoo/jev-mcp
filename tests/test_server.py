"""Smoke tests with a mocked TypeSafe API (no key or network needed)."""
import json
import os

import pytest
import respx
from httpx import Response

os.environ.setdefault("JEV_AI_API_KEY", "test-key")

from jev_mcp import client, server  # noqa: E402
from jev_mcp.server import EvaluateInput, RouteInput  # noqa: E402

ENDPOINT = "https://jev-ai.pro/api/v1/systemone"


@pytest.mark.asyncio
@respx.mock
async def test_evaluate_noul():
    respx.post(ENDPOINT).mock(
        return_value=Response(200, json={
            "model": "jev-latest",
            "answers": {"is_urgent": {"type": "noul", "noul": 0.92}},
            "usage": {"input_tokens": 10, "output_tokens": 2},
        })
    )
    out = await server.jev_evaluate(EvaluateInput(
        state="Help! Payouts failing 3 days.",
        questions={"is_urgent": {"type": "noul", "instructions": "Is this urgent?"}},
    ))
    assert json.loads(out)["answers"]["is_urgent"]["noul"] == 0.92


@pytest.mark.asyncio
@respx.mock
async def test_route_escalates_below_threshold():
    respx.post(ENDPOINT).mock(
        return_value=Response(200, json={
            "model": "jev-latest",
            "answers": {"route": {
                "type": "choice", "choice": "technical",
                "probabilities": {"billing": 0.4, "technical": 0.6},
                "confidence": 0.55,
            }},
            "usage": {"input_tokens": 10, "output_tokens": 2},
        })
    )
    out = json.loads(await server.jev_route(RouteInput(
        state="Something is wrong",
        instructions="Which team?",
        options={"billing": None, "technical": None},
        confidence_threshold=0.8,
    )))
    assert out["choice"] == "technical" and out["escalate"] is True


def test_choice_requires_criteria():
    with pytest.raises(ValueError):
        EvaluateInput(state="x", questions={"q": {"type": "choice", "instructions": "?"}})


def test_dotenv_parses_and_ignores_comments(tmp_path):
    p = tmp_path / ".env"
    p.write_text(
        '# a comment\n'
        'JEV_AI_API_KEY="sk_from_file"\n'
        "\n"
        "export JEV_AI_API_BASE='https://example.test/api'\n"
        "MALFORMED_NO_EQUALS\n"
    )
    vals = client.dotenv_values(p)
    assert vals["JEV_AI_API_KEY"] == "sk_from_file"
    assert vals["JEV_AI_API_BASE"] == "https://example.test/api"
    assert "MALFORMED_NO_EQUALS" not in vals


def test_dotenv_missing_file_is_empty(tmp_path):
    assert client.dotenv_values(tmp_path / "nope.env") == {}


def test_env_var_beats_dotenv(monkeypatch):
    monkeypatch.setenv("JEV_AI_API_KEY", "sk_from_env")
    monkeypatch.setattr(client, "dotenv_values", lambda *a, **k: {"JEV_AI_API_KEY": "sk_from_file"})
    assert client.JevClient().api_key == "sk_from_env"


def test_dotenv_used_when_env_var_absent(monkeypatch):
    monkeypatch.delenv("JEV_AI_API_KEY", raising=False)
    monkeypatch.setattr(client, "dotenv_values", lambda *a, **k: {"JEV_AI_API_KEY": "sk_from_file"})
    assert client.JevClient().api_key == "sk_from_file"


def test_missing_key_everywhere_raises(monkeypatch):
    monkeypatch.delenv("JEV_AI_API_KEY", raising=False)
    monkeypatch.setattr(client, "dotenv_values", lambda *a, **k: {})
    with pytest.raises(client.JevError, match="JEV_AI_API_KEY is not set"):
        client.JevClient()
