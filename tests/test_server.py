"""Smoke tests with a mocked TypeSafe API (no key or network needed)."""
import json
import os

import pytest
import respx
from httpx import Response

os.environ.setdefault("JEV_AI_API_KEY", "test-key")

from jev_mcp import server  # noqa: E402
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
