"""jev_mcp — MCP server (mcp SDK 2.x) exposing TypeSafe AI's Jev decision model.

Run locally (stdio):  jev-mcp
Or:                   python -m jev_mcp.server
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .client import DEFAULT_MODEL, JevClient, JevError
from .images import ImageError, expand_paths, load_image

mcp = MCPServer(
    "jev_mcp",
    instructions=(
        "Fast typed decisions (yes/no, choice, score) and object location (box) from TypeSafe AI's Jev, "
        "on text or local PNG/JPEG images. Not for text generation."
    ),
)
BATCH_CONCURRENCY = 8
MAX_BATCH_IMAGES = 1000
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

    type: Literal["noul", "choice", "score", "box"] = Field(
        description=(
            "noul = yes/no probability; choice = pick one option; score = rate on an ordered rubric; "
            "box = locate what the instructions describe in the image (needs an image, no criteria)"
        )
    )
    instructions: str = Field(min_length=1, description="What the model should decide, e.g. 'Which team should handle this?'")
    criteria: dict[str, str | None] | list[str] | None = Field(
        default=None,
        description=(
            "noul: optional {'true': '...', 'false': '...'}. "
            "choice: REQUIRED map of option -> description (or null). "
            "score: REQUIRED ordered list of 2+ level descriptions, low to high. "
            "box: must be omitted."
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
        if self.type == "box" and self.criteria is not None:
            raise ValueError("box questions take no criteria; describe what to locate in instructions")
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
    images: dict[str, str] | None = Field(
        default=None,
        description=(
            "Optional map of placeholder -> local PNG/JPEG path (max 16 MB). The server reads the file; "
            "each placeholder (e.g. 'IMG_receipt') must appear inside state, an instructions or a criteria "
            "string, and Jev sees the image there. Box questions need at least one image."
        ),
    )
    model: str = Field(default=DEFAULT_MODEL, description="Jev model alias, e.g. 'jev-latest'.")

    @model_validator(mode="after")
    def _check_images(self) -> "EvaluateInput":
        if self.images:
            text = json.dumps(
                [self.state, [q.model_dump(exclude_none=True) for q in self.questions.values()]],
                ensure_ascii=False,
            )
            missing = [ph for ph in self.images if ph not in text]
            if missing:
                raise ValueError(f"image placeholders not used in state, instructions or criteria: {missing}")
        elif any(q.type == "box" for q in self.questions.values()):
            raise ValueError("box questions need an image: pass images={placeholder: path} and use the placeholder in state")
        return self


class EvaluateImagesInput(BaseModel):
    """One question set applied to many images, one Jev call per image."""

    model_config = ConfigDict(extra="forbid")

    paths: list[str] = Field(
        min_length=1,
        description="PNG/JPEG files and/or directories (a directory adds its *.png/*.jpg/*.jpeg, not recursive, sorted).",
    )
    questions: dict[str, Question] = Field(min_length=1, description="Asked about every image; answers come back under the same ids.")
    context: str | None = Field(
        default=None,
        description="Optional text sent before each image, e.g. 'Customer photo of a delivered parcel.'",
    )
    confidence_threshold: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description=(
            "If set, each result lists question ids answered below it in review (choice/score/box confidence; "
            "noul uses max(p, 1-p)), so uncertain images can go to Claude or a human."
        ),
    )
    model: str = Field(default=DEFAULT_MODEL)


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
        assets = {ph: load_image(path) for ph, path in (params.images or {}).items()} or None
        result = await get_client().evaluate(
            state=params.state,
            questions={k: q.model_dump(exclude_none=True) for k, q in params.questions.items()},
            model=params.model,
            assets=assets,
        )
    except (JevError, ImageError) as e:
        return json.dumps({"error": str(e)})
    return json.dumps(result, indent=2)


def answer_confidence(answer: dict[str, Any]) -> float | None:
    if answer.get("type") == "noul":
        p = answer["noul"]
        return max(p, 1 - p)
    return answer.get("confidence")


@mcp.tool(
    name="jev_evaluate_images",
    annotations=ToolAnnotations(title="Evaluate many images with Jev", readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True),
)
async def jev_evaluate_images(params: EvaluateImagesInput) -> str:
    """Ask the same typed questions (noul/choice/score/box) about many local images:
    sort a photo folder, check rendered pages, flag receipts, find a face per video
    frame (extract frames with ffmpeg first). Files are read by the server, never
    passed through the conversation. Box answers are fractions of image height/width.

    Returns JSON: {"model", "count", "failed", "results": [{"path", "answers", "review"?} | {"path", "error"}], "usage"}.
    """
    files = expand_paths(params.paths)
    if not files:
        return json.dumps({"error": "no PNG/JPEG files found in the given paths"})
    if len(files) > MAX_BATCH_IMAGES:
        return json.dumps({"error": f"{len(files)} images is over the {MAX_BATCH_IMAGES} per-call cap; split the batch"})
    questions = {k: q.model_dump(exclude_none=True) for k, q in params.questions.items()}
    sem = asyncio.Semaphore(BATCH_CONCURRENCY)
    try:
        client = get_client()
    except JevError as e:
        return json.dumps({"error": str(e)})

    async def one(path) -> dict[str, Any]:
        ph = f"IMG_{uuid.uuid4().hex}"
        state = f"{params.context}\n{ph}" if params.context else ph
        try:
            asset = load_image(path)
            async with sem:
                res = await client.evaluate(state=state, questions=questions, model=params.model, assets={ph: asset})
        except (JevError, ImageError) as e:
            return {"path": str(path), "error": str(e)}
        item: dict[str, Any] = {"path": str(path), "answers": res["answers"], "_model": res.get("model"), "_usage": res.get("usage", {})}
        if params.confidence_threshold is not None:
            item["review"] = [
                qid for qid, a in res["answers"].items()
                if (c := answer_confidence(a)) is not None and c < params.confidence_threshold
            ]
        return item

    results = await asyncio.gather(*(one(f) for f in files))
    usage = {"input_tokens": 0, "output_tokens": 0}
    model = None
    for r in results:
        u = r.pop("_usage", {})
        model = r.pop("_model", None) or model
        for k in usage:
            usage[k] += u.get(k, 0)
    return json.dumps(
        {
            "model": model,
            "count": len(results),
            "failed": sum(1 for r in results if "error" in r),
            "results": results,
            "usage": usage,
        },
        indent=2,
    )


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
