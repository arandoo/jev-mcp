"""jev_mcp — MCP server (mcp SDK 2.x) exposing TypeSafe AI's Jev decision model.

Run locally (stdio):  jev-mcp
Or:                   python -m jev_mcp.server
"""

from __future__ import annotations

import json
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .client import DEFAULT_MODEL, JevClient, JevError

mcp = MCPServer("jev_mcp", instructions="Fast typed decisions (yes/no, choice, score) from TypeSafe AI's Jev. Not for text generation.")
_client: JevClient | None = None


def get_client() -> JevClient:
    global _client
    if _client is None:
        _client = JevClient()
    return _client


# ---------- Input models ----------


class Question(BaseModel):
    """One typed question. Matches TypeSafe's Question schema."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["noul", "choice", "score"] = Field(
        description="noul = yes/no probability; choice = pick one option; score = rate on an ordered rubric"
    )
    instructions: str = Field(min_length=1, description="What the model should decide, e.g. 'Which team should handle this?'")
    criteria: dict[str, str | None] | list[str] | None = Field(
        default=None,
        description=(
            "noul: optional {'true': '...', 'false': '...'}. "
            "choice: REQUIRED map of option -> description (or null). "
            "score: REQUIRED ordered list of 2+ level descriptions, low to high."
        ),
    )

    @model_validator(mode="after")
    def _check_criteria(self) -> "Question":
        if self.type == "choice" and not isinstance(self.criteria, dict):
            raise ValueError("choice questions need criteria as a map of option -> description")
        if self.type == "score" and (not isinstance(self.criteria, list) or len(self.criteria) < 2):
            raise ValueError("score questions need criteria as an ordered list of at least 2 levels")
        if self.type == "noul" and self.criteria is not None and not isinstance(self.criteria, dict):
            raise ValueError("noul criteria, if given, must be {'true': ..., 'false': ...}")
        return self


class EvaluateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: str | dict[str, Any] | list[Any] = Field(
        description="Content to evaluate: plain text, or a JSON object/array (ticket, record, chat log)."
    )
    questions: dict[str, Question] = Field(
        min_length=1,
        description="Map of your own question id -> Question. Answers come back under the same ids.",
    )
    model: str = Field(default=DEFAULT_MODEL, description="Jev model alias, e.g. 'jev-latest'.")


class RouteInput(BaseModel):
    """Convenience: one choice question + confidence threshold, for gating pipelines."""

    model_config = ConfigDict(extra="forbid")

    state: str | dict[str, Any] | list[Any] = Field(description="Content to route.")
    instructions: str = Field(description="Routing question, e.g. 'Which team should handle this?'")
    options: dict[str, str | None] = Field(min_length=2, description="option -> description")
    confidence_threshold: float = Field(default=0.8, ge=0, le=1, description="Below this, escalate=true.")
    model: str = Field(default=DEFAULT_MODEL)


# ---------- Tools ----------


@mcp.tool(
    name="jev_evaluate",
    annotations=ToolAnnotations(title="Evaluate state with Jev", readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True),
)
async def jev_evaluate(params: EvaluateInput) -> str:
    """Send a state plus one or more typed questions (noul/choice/score) to Jev and
    get back typed answers with probabilities and confidence. Use this for fast,
    cheap classification, routing, urgency scoring, or yes/no checks — not for
    generating text.

    Returns JSON: {"model": ..., "answers": {id: Answer}, "usage": {...}}.
    """
    try:
        result = await get_client().evaluate(
            state=params.state,
            questions={k: q.model_dump(exclude_none=True) for k, q in params.questions.items()},
            model=params.model,
        )
    except JevError as e:
        return json.dumps({"error": str(e)})
    return json.dumps(result, indent=2)


@mcp.tool(
    name="jev_route",
    annotations=ToolAnnotations(title="Route with confidence gate", readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True),
)
async def jev_route(params: RouteInput) -> str:
    """Ask Jev a single routing (choice) question and apply a confidence threshold.
    Returns {"choice", "confidence", "probabilities", "escalate"} where escalate is
    true when confidence < threshold — i.e. hand off to Claude or a human.
    """
    try:
        result = await get_client().evaluate(
            state=params.state,
            questions={
                "route": {
                    "type": "choice",
                    "instructions": params.instructions,
                    "criteria": params.options,
                }
            },
            model=params.model,
        )
    except JevError as e:
        return json.dumps({"error": str(e)})
    ans = result["answers"]["route"]
    return json.dumps(
        {
            "choice": ans["choice"],
            "confidence": ans["confidence"],
            "probabilities": ans["probabilities"],
            "escalate": ans["confidence"] < params.confidence_threshold,
            "model": result["model"],
        },
        indent=2,
    )


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
