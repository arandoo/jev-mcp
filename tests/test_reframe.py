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
