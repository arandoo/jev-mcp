"""Smoke tests with a mocked TypeSafe API (no key or network needed)."""
import asyncio
import json
import os

import pytest
import respx
from httpx import Response

os.environ.setdefault("JEV_AI_API_KEY", "test-key")
# Pin the base so a developer .env (e.g. JEV_AI_API_BASE=https://api.typesafe.ai) can't
# redirect mocked calls; env vars win over .env.
os.environ["JEV_AI_API_BASE"] = "https://jev-ai.pro/api"

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


def test_dotenv_tolerates_utf8_bom(tmp_path):
    """PowerShell's Set-Content -Encoding utf8 prepends a BOM."""
    p = tmp_path / ".env"
    p.write_bytes(b"\xef\xbb\xbfJEV_AI_API_KEY=sk_bom\n")
    assert client.dotenv_values(p)["JEV_AI_API_KEY"] == "sk_bom"


def test_dotenv_tolerates_crlf(tmp_path):
    """Windows editors write CRLF line endings."""
    p = tmp_path / ".env"
    p.write_bytes(b"JEV_AI_API_KEY=sk_crlf\r\nJEV_AI_API_BASE=https://x.test\r\n")
    vals = client.dotenv_values(p)
    assert vals["JEV_AI_API_KEY"] == "sk_crlf"
    assert vals["JEV_AI_API_BASE"] == "https://x.test"


def test_dotenv_undecodable_file_is_empty(tmp_path):
    """A UTF-16 .env (PowerShell 5.1 Out-File default) must not crash the server."""
    p = tmp_path / ".env"
    p.write_bytes("JEV_AI_API_KEY=sk_utf16\n".encode("utf-16"))
    assert client.dotenv_values(p) == {}


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


# ---------- images / box ----------

import base64  # noqa: E402
from pathlib import Path  # noqa: E402

from jev_mcp.server import EvaluateImagesInput  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16


def _box_response(confidence=0.9):
    return Response(200, json={
        "model": "jev-latest",
        "answers": {
            "damaged": {"type": "noul", "noul": 0.97},
            "where": {"type": "box", "box": {"top": 0.2, "bottom": 0.6, "left": 0.1, "right": 0.5}, "confidence": confidence},
        },
        "usage": {"input_tokens": 300, "output_tokens": 4},
    })


@pytest.mark.asyncio
@respx.mock
async def test_evaluate_sends_image_asset_and_header(tmp_path):
    img = tmp_path / "parcel.png"
    img.write_bytes(PNG)
    route = respx.post(ENDPOINT).mock(return_value=_box_response())
    out = json.loads(await server.jev_evaluate(EvaluateInput(
        state="Customer photo: IMG_parcel",
        questions={
            "damaged": {"type": "noul", "instructions": "Is the parcel damaged?"},
            "where": {"type": "box", "instructions": "The damaged area"},
        },
        images={"IMG_parcel": str(img)},
    )))
    assert out["answers"]["where"]["box"]["top"] == 0.2
    req = route.calls.last.request
    assert req.headers["TypeSafe-Danger-Zone-Images"] == "true"
    body = json.loads(req.content)
    asset = body["assets"]["IMG_parcel"]
    assert asset == {"type": "image", "format": "png", "data_base64": base64.b64encode(PNG).decode()}
    assert "criteria" not in body["questions"]["where"]


@pytest.mark.asyncio
@respx.mock
async def test_text_only_request_has_no_images_header():
    route = respx.post(ENDPOINT).mock(return_value=Response(200, json={
        "model": "jev-latest", "answers": {"q": {"type": "noul", "noul": 0.1}}, "usage": {},
    }))
    await server.jev_evaluate(EvaluateInput(state="hi", questions={"q": {"type": "noul", "instructions": "?"}}))
    req = route.calls.last.request
    assert "TypeSafe-Danger-Zone-Images" not in req.headers
    assert "assets" not in json.loads(req.content)


def test_box_rejects_criteria():
    with pytest.raises(ValueError, match="no criteria"):
        EvaluateInput(state="IMG_a", images={"IMG_a": "/x.png"},
                      questions={"b": {"type": "box", "instructions": "face", "criteria": {"x": "y"}}})


def test_box_without_image_rejected():
    with pytest.raises(ValueError, match="need an image"):
        EvaluateInput(state="text", questions={"b": {"type": "box", "instructions": "face"}})


def test_unused_placeholder_rejected():
    with pytest.raises(ValueError, match="not used"):
        EvaluateInput(state="no placeholder here", images={"IMG_a": "/x.png"},
                      questions={"q": {"type": "noul", "instructions": "?"}})


def test_placeholder_in_criteria_counts():
    EvaluateInput(state="Which matches?", images={"IMG_ref": "/x.png"},
                  questions={"q": {"type": "choice", "instructions": "?", "criteria": {"same": "Looks like IMG_ref", "other": None}}})


@pytest.mark.asyncio
async def test_non_image_file_returns_actionable_error(tmp_path):
    f = tmp_path / "pic.webp"
    f.write_bytes(b"RIFF\x00\x00\x00\x00WEBPVP8 ")
    out = json.loads(await server.jev_evaluate(EvaluateInput(
        state="IMG_a", images={"IMG_a": str(f)}, questions={"q": {"type": "noul", "instructions": "?"}},
    )))
    assert "only PNG or JPEG" in out["error"]


def test_oversize_image_rejected(tmp_path, monkeypatch):
    from jev_mcp import images
    f = tmp_path / "big.png"
    f.write_bytes(PNG)
    monkeypatch.setattr(images, "MAX_IMAGE_BYTES", 10)
    with pytest.raises(images.ImageError, match="16 MB"):
        images.load_image(f)


def test_jpeg_sniffed_regardless_of_suffix(tmp_path):
    from jev_mcp import images
    f = tmp_path / "frame.png"  # wrong suffix on purpose
    f.write_bytes(JPEG)
    assert images.load_image(f)["format"] == "jpeg"


@pytest.mark.asyncio
@respx.mock
async def test_batch_expands_directory_flags_review_and_sums_usage(tmp_path):
    d = tmp_path / "frames"
    d.mkdir()
    (d / "f_0001.png").write_bytes(PNG)
    (d / "f_0002.jpg").write_bytes(JPEG)
    (d / "notes.txt").write_text("ignored")
    bad = tmp_path / "broken.png"
    bad.write_bytes(b"not an image")
    respx.post(ENDPOINT).mock(side_effect=[_box_response(0.9), _box_response(0.3)])
    out = json.loads(await server.jev_evaluate_images(EvaluateImagesInput(
        paths=[str(d), str(bad)],
        context="Delivery photo.",
        questions={
            "damaged": {"type": "noul", "instructions": "Is the parcel damaged?"},
            "where": {"type": "box", "instructions": "The damaged area"},
        },
        confidence_threshold=0.8,
    )))
    assert out["count"] == 3 and out["failed"] == 1
    assert [Path(r["path"]).name for r in out["results"]] == ["f_0001.png", "f_0002.jpg", "broken.png"]
    reviews = sorted(tuple(r["review"]) for r in out["results"] if "review" in r)
    assert reviews == [(), ("where",)]
    assert out["usage"] == {"input_tokens": 600, "output_tokens": 8}
    for call in respx.calls:
        body = json.loads(call.request.content)
        (ph,) = body["assets"]
        assert body["state"] == f"Delivery photo.\n{ph}"
        assert call.request.headers["TypeSafe-Danger-Zone-Images"] == "true"


@pytest.mark.asyncio
async def test_batch_with_no_images_errors(tmp_path):
    out = json.loads(await server.jev_evaluate_images(EvaluateImagesInput(
        paths=[str(tmp_path)], questions={"q": {"type": "noul", "instructions": "?"}},
    )))
    assert "no PNG/JPEG" in out["error"]


@pytest.mark.asyncio
@respx.mock
async def test_batch_isolates_odd_response_and_unreadable_file(tmp_path, monkeypatch):
    from jev_mcp import images
    ok = tmp_path / "a_ok.png"
    ok.write_bytes(PNG)
    odd = tmp_path / "b_odd.png"
    odd.write_bytes(PNG)
    locked = tmp_path / "c_locked.png"
    locked.write_bytes(PNG)
    real_read = Path.read_bytes

    def read_bytes(self):
        if self.name == "c_locked.png":
            raise PermissionError(13, "Permission denied")
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    respx.post(ENDPOINT).mock(side_effect=[
        _box_response(),
        Response(200, json={"model": "jev-latest", "usage": {}}),
    ])
    out = json.loads(await server.jev_evaluate_images(EvaluateImagesInput(
        paths=[str(tmp_path)],
        questions={"damaged": {"type": "noul", "instructions": "?"}},
        confidence_threshold=0.5,
    )))
    by_name = {Path(r["path"]).name: r for r in out["results"]}
    assert "answers" in by_name["a_ok.png"]
    assert "unexpected Jev response" in by_name["b_odd.png"]["error"]
    assert "cannot read" in by_name["c_locked.png"]["error"]
    assert out["failed"] == 2
    assert isinstance(images.ImageError("x"), ValueError)


@pytest.mark.asyncio
@respx.mock
async def test_batch_loads_at_most_concurrency_images_at_once(tmp_path, monkeypatch):
    from jev_mcp import images
    for i in range(20):
        (tmp_path / f"f_{i:02d}.png").write_bytes(PNG)
    live = 0
    peak = 0
    real_load = images.load_image

    def counting_load(path):
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        return real_load(path)

    async def slow(request):
        nonlocal live
        await asyncio.sleep(0.01)
        live -= 1
        return _box_response()

    monkeypatch.setattr(server, "load_image", counting_load)
    respx.post(ENDPOINT).mock(side_effect=slow)
    out = json.loads(await server.jev_evaluate_images(EvaluateImagesInput(
        paths=[str(tmp_path)], questions={"damaged": {"type": "noul", "instructions": "?"}},
    )))
    assert out["failed"] == 0
    assert peak <= server.BATCH_CONCURRENCY


@pytest.mark.parametrize("images_map, state", [
    ({"type": "/x.png"}, "no placeholder"),          # matched a JSON key before
    ({"IMG": "/x.png"}, "IMG"),                       # too short
    ({"IMG_1": "/a.png", "IMG_12": "/b.png"}, "IMG_1 IMG_12"),  # nested placeholders
])
def test_bad_placeholders_rejected(images_map, state):
    with pytest.raises(ValueError):
        EvaluateInput(state=state, images=images_map, questions={"q": {"type": "noul", "instructions": "?"}})


def test_placeholder_with_backslash_or_quote_accepted():
    EvaluateInput(state='see C:\\img and "IMG_q"', images={"C:\\img": "/a.png", '"IMG_q"': "/b.png"},
                  questions={"q": {"type": "noul", "instructions": "?"}})
