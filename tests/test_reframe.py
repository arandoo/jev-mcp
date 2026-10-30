"""Pure tests for the reframe path planner (no ffmpeg, no network)."""
import os

os.environ.setdefault("JEV_AI_API_KEY", "test-key")

from jev_mcp.reframe import Sample, parse_cut_times, plan_path  # noqa: E402

W, CW = 1920, 608


def xs_between(path, a, b):
    return [x for t, x in path if a <= t < b]


def test_parse_cut_times_from_showinfo():
    log = "[Parsed_showinfo_1] n:0 pts:1000 pts_time:31.25 duration ...\n... pts_time:104.833333 ..."
    assert parse_cut_times(log) == [31.25, 104.833333]


def test_jumps_at_cut_instead_of_averaging_across_it():
    samples = [Sample(t / 3, 0.2, 0.9) for t in range(0, 30)] + [Sample(t / 3, 0.8, 0.9) for t in range(30, 60)]
    path = plan_path(samples, cuts=[10.0], duration=20.0, width=W, crop_w=CW)
    before, after = xs_between(path, 9.5, 10.0), xs_between(path, 10.0, 10.5)
    left_x = round(0.2 * W - CW / 2)
    right_x = round(0.8 * W - CW / 2)
    assert all(abs(x - left_x) <= 2 for x in before)
    assert all(abs(x - right_x) <= 2 for x in after)  # no lag after the cut


def test_without_cut_the_same_move_is_smoothed():
    samples = [Sample(t / 3, 0.2, 0.9) for t in range(0, 30)] + [Sample(t / 3, 0.8, 0.9) for t in range(30, 60)]
    path = plan_path(samples, cuts=[], duration=20.0, width=W, crop_w=CW)
    mid = xs_between(path, 9.9, 10.1)
    assert all(round(0.2 * W - CW / 2) + 10 < x < round(0.8 * W - CW / 2) - 10 for x in mid)


def test_low_confidence_samples_are_ignored():
    samples = [Sample(t / 3, 0.3, 0.9) for t in range(0, 60)]
    samples[30] = Sample(10.0, 0.95, 0.05)  # wild, unconfident box
    path = plan_path(samples, cuts=[], duration=20.0, width=W, crop_w=CW)
    assert len(set(x for _, x in path)) == 1


def test_dead_zone_absorbs_small_wobble():
    samples = [Sample(t / 3, 0.5 + (0.02 if t % 2 else -0.02), 0.9) for t in range(0, 60)]
    path = plan_path(samples, cuts=[], duration=20.0, width=W, crop_w=CW)
    assert len(set(x for _, x in path)) == 1


def test_shot_without_confident_samples_holds_previous_position():
    samples = [Sample(t / 3, 0.25, 0.9) for t in range(0, 30)] + [Sample(t / 3, 0.9, 0.05) for t in range(30, 60)]
    path = plan_path(samples, cuts=[10.0], duration=20.0, width=W, crop_w=CW)
    assert set(xs_between(path, 10.0, 20.0)) == {xs_between(path, 9.9, 10.0)[-1]}


def test_crop_stays_inside_frame():
    samples = [Sample(t / 3, 0.0 if t < 30 else 1.0, 0.9) for t in range(0, 60)]
    path = plan_path(samples, cuts=[10.0], duration=20.0, width=W, crop_w=CW)
    assert min(x for _, x in path) == 0 and max(x for _, x in path) == W - CW


def test_tool_returns_error_json_for_missing_video(tmp_path):
    import asyncio
    import json
    from jev_mcp import server
    out = json.loads(asyncio.run(server.jev_reframe_video(server.ReframeInput(
        video=str(tmp_path / "nope.mp4"), out=str(tmp_path / "o.mp4"),
    ))))
    assert "error" in out


# ---------- review follow-up: mocked API, timestamps, validation ----------

import asyncio  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
import respx  # noqa: E402
from httpx import Response  # noqa: E402

from jev_mcp import client as jev_client  # noqa: E402
from jev_mcp import reframe as rf  # noqa: E402

ENDPOINT = "https://jev-ai.pro/api/v1/systemone"
os.environ["JEV_AI_API_BASE"] = "https://jev-ai.pro/api"


def _frames(tmp_path, n):
    from PIL import Image
    out = []
    for k in range(n):
        p = tmp_path / f"f_{k:05d}.jpg"
        Image.new("RGB", (64, 36), (k * 10 % 255, 80, 80)).save(p, format="JPEG")
        out.append((k / 3, p))
    return out


def _box(left, right, conf=0.6):
    return Response(200, json={"model": "jev-latest", "usage": {},
                               "answers": {"where": {"type": "box", "confidence": conf,
                                                     "box": {"top": 0.1, "bottom": 0.9, "left": left, "right": right}}}})


@respx.mock
def test_locate_aborts_on_auth_failure_instead_of_rendering_blind(tmp_path):
    respx.post(ENDPOINT).mock(return_value=Response(401, text="bad key"))
    with pytest.raises(jev_client.JevError, match="located 0 of 4.*401"):
        asyncio.run(rf.locate(_frames(tmp_path, 4), "hands"))


@respx.mock
def test_locate_tolerates_a_few_failed_frames(tmp_path):
    respx.post(ENDPOINT).mock(side_effect=[_box(0.1, 0.3), Response(500, text="x"), _box(0.5, 0.7), _box(0.6, 0.8)])
    got = asyncio.run(rf.locate(_frames(tmp_path, 4), "hands", concurrency=1))
    assert [round(s.cx, 2) for s in got] == [0.2, 0.6, 0.7]


@respx.mock
def test_locate_aborts_when_most_frames_fail(tmp_path):
    respx.post(ENDPOINT).mock(side_effect=[_box(0.1, 0.3)] + [Response(422, text="bad image")] * 3)
    with pytest.raises(jev_client.JevError, match="located 1 of 4"):
        asyncio.run(rf.locate(_frames(tmp_path, 4), "hands", concurrency=1))


def test_pair_frames_uses_real_timestamps_after_preroll(tmp_path):
    """Video starting 0.7 s after audio: fps=3 emits frames at 0.667, 1.0, 1.333."""
    files = [tmp_path / f"f_{k}.jpg" for k in range(3)]
    log = "n:0 pts:20 pts_time:0.666667 x\nn:1 pts:30 pts_time:1 x\nn:2 pts:40 pts_time:1.33333 x\n"
    assert [round(t, 3) for t, _ in rf.pair_frames(files, log, 3)] == [0.667, 1.0, 1.333]


def test_pair_frames_falls_back_when_log_does_not_match(tmp_path):
    files = [tmp_path / f"f_{k}.jpg" for k in range(3)]
    assert [t for t, _ in rf.pair_frames(files, "nothing", 2)] == [0.0, 0.5, 1.0]


def test_probe_of_audio_only_file_is_a_clear_error():
    with pytest.raises(ValueError, match="not a video"):
        rf.parse_probe(json.dumps({"streams": [], "format": {"duration": "3.0"}}), "song.m4a")


def test_tool_rejects_zero_aspect_with_error_json(tmp_path):
    from jev_mcp import server
    out = json.loads(asyncio.run(server.jev_reframe_video(server.ReframeInput(
        video=str(tmp_path / "v.mp4"), out=str(tmp_path / "o.mp4"), aspect="9:0",
    ))))
    assert "positive" in out["error"]
