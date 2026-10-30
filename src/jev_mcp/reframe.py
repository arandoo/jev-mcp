"""Auto-reframe a landscape video to a vertical crop that follows what Jev locates.

Pipeline: ffmpeg samples frames and finds hard cuts, Jev answers a box question
per frame, plan_path turns the boxes into a smooth crop path that jumps at cuts
and moves only when the subject leaves a dead zone, ffmpeg renders the crop.

CLI:  jev-reframe in.mp4 out.mp4 [--target "..."] [--fps 3] [--aspect 9:16]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from .client import JevClient, JevError
from .images import ImageError, load_image

DEFAULT_TARGET = "The hands and the tool or material they are working on"
PATH_HZ = 25  # crop path resolution; one sendcmd line per step
MIN_CONF = 0.15  # box confidence below this is treated as "not located"
MAX_FAILED_SHARE = 0.5  # more failed Jev calls than this aborts instead of rendering a blind crop


@dataclass
class Sample:
    t: float  # seconds
    cx: float  # box center, fraction of width
    conf: float | None


def parse_probe(probe_json: str, name: str = "input") -> tuple[int, int, float]:
    try:
        d = json.loads(probe_json)
        s = d["streams"][0]
        return int(s["width"]), int(s["height"]), float(d["format"]["duration"])
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise ValueError(f"{name}: not a video with a known duration ({e!r})") from e


def ffprobe_video(video: Path) -> tuple[int, int, float]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-show_entries", "format=duration", "-of", "json", str(video)],
        check=True, capture_output=True, text=True,
    ).stdout
    return parse_probe(out, video.name)


def parse_cut_times(showinfo_log: str) -> list[float]:
    return [float(m) for m in re.findall(r"pts_time:([0-9.]+)", showinfo_log)]


def detect_cuts(video: Path, threshold: float) -> list[float]:
    """Hard cuts via ffmpeg's scene score (0-1). Soft fades mostly stay below 0.25."""
    log = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(video), "-an",
         "-vf", f"select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
        check=True, capture_output=True, text=True,
    ).stderr
    return parse_cut_times(log)


def pair_frames(frames: list[Path], showinfo_log: str, fps: float) -> list[tuple[float, Path]]:
    """Attach each extracted frame's real timestamp from showinfo.

    ffmpeg shifts input by the container start time, so when video starts after
    audio (pre-roll), frame k is not at k / fps. Falls back to k / fps only if
    the log can't be matched one-to-one.
    """
    times = parse_cut_times(showinfo_log)
    if len(times) == len(frames):
        return list(zip(times, frames))
    return [(k / fps, p) for k, p in enumerate(frames)]


def extract_frames(video: Path, fps: float, outdir: Path) -> list[tuple[float, Path]]:
    log = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-y", "-i", str(video), "-an", "-vf", f"fps={fps},showinfo",
         "-q:v", "3", "-fps_mode", "passthrough", str(outdir / "f_%05d.jpg")],
        check=True, capture_output=True, text=True,
    ).stderr
    return pair_frames(sorted(outdir.glob("f_*.jpg")), log, fps)


async def locate(frames: list[tuple[float, Path]], target: str, concurrency: int = 8) -> list[Sample]:
    client = JevClient()
    sem = asyncio.Semaphore(concurrency)
    question = {"where": {"type": "box", "instructions": target}}

    async def one(t: float, path: Path) -> Sample | JevError | None:
        ph = f"IMG_{uuid.uuid4().hex}"
        async with sem:
            try:
                asset = await asyncio.to_thread(load_image, path)
                res = await client.evaluate(state=ph, questions=question, assets={ph: asset})
                a = res["answers"]["where"]
                return Sample(t, (a["box"]["left"] + a["box"]["right"]) / 2, a.get("confidence"))
            except JevError as e:
                return e
            except (ImageError, KeyError, TypeError):
                return None

    try:
        got = await asyncio.gather(*(one(t, p) for t, p in frames))
    finally:
        await client.aclose()
    errors = [g for g in got if isinstance(g, JevError)]
    samples = [g for g in got if isinstance(g, Sample)]
    if frames and (not samples or len(errors) > MAX_FAILED_SHARE * len(frames)):
        first = f": {errors[0]}" if errors else ""
        raise JevError(f"Jev located {len(samples)} of {len(frames)} frames ({len(errors)} API errors){first}")
    return samples


def plan_path(
    samples: list[Sample],
    cuts: list[float],
    duration: float,
    width: int,
    crop_w: int,
    *,
    min_conf: float = MIN_CONF,
    smooth_s: float = 1.0,
    deadzone: float = 0.12,
    hz: int = PATH_HZ,
) -> list[tuple[float, int]]:
    """Crop x (left edge, px) at hz steps.

    Per shot (between cuts): samples below min_conf are ignored, targets are
    linearly interpolated, smoothed with a centered moving average that never
    crosses a cut, then followed with a dead zone (fraction of crop width) so
    small wobbles don't move the frame. At a cut the frame jumps to the new
    target. A shot with no confident sample keeps the previous position.
    """
    max_x = width - crop_w
    steps = [i / hz for i in range(int(duration * hz) + 1)]
    bounds = [0.0] + sorted(c for c in cuts if 0 < c < duration) + [duration + 1e-9]
    path: list[tuple[float, int]] = []
    pos = width / 2  # crop center in px
    win = max(1, int(smooth_s * hz))
    dz = deadzone * crop_w

    for a, b in zip(bounds, bounds[1:]):
        ts = [t for t in steps if a <= t < b]
        if not ts:
            continue
        good = [(s.t, s.cx * width) for s in samples if a <= s.t < b and s.conf is not None and s.conf >= min_conf]
        if not good:
            targets = [pos] * len(ts)
        else:
            targets = []
            for t in ts:
                if t <= good[0][0]:
                    targets.append(good[0][1])
                elif t >= good[-1][0]:
                    targets.append(good[-1][1])
                else:
                    j = next(i for i in range(1, len(good)) if good[i][0] >= t)
                    (t0, x0), (t1, x1) = good[j - 1], good[j]
                    targets.append(x0 + (x1 - x0) * (t - t0) / (t1 - t0))
            half = win // 2
            targets = [
                sum(targets[max(0, i - half):i + half + 1]) / len(targets[max(0, i - half):i + half + 1])
                for i in range(len(targets))
            ]
            pos = targets[0]  # jump at the cut
        for t, target in zip(ts, targets):
            if target - pos > dz:
                pos = target - dz
            elif pos - target > dz:
                pos = target + dz
            path.append((t, int(round(min(max(pos - crop_w / 2, 0), max_x)))))
    return path


def render(video: Path, out: Path, path: list[tuple[float, int]], crop_w: int, crop_h: int, workdir: Path) -> None:
    cmd_file = workdir / "crop.cmd"
    cmd_file.write_text("\n".join(f"{t:.3f} crop x {x};" for t, x in path))
    # sendcmd parses its file path as a filter option: keep it relative to cwd
    # so spaces or colons in absolute paths can't break the filtergraph.
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-i", str(video.resolve()),
         "-filter_complex", f"[0:v]sendcmd=f=crop.cmd,crop={crop_w}:{crop_h}:{path[0][1]}:0[v]",
         "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-crf", "20", "-preset", "veryfast",
         "-c:a", "aac", str(out.resolve())],
        check=True, cwd=workdir,
    )


async def reframe_async(
    video: str | Path,
    out: str | Path,
    *,
    target: str = DEFAULT_TARGET,
    fps: float = 3.0,
    aspect: str = "9:16",
    scene_threshold: float = 0.25,
) -> dict:
    video, out = Path(video).expanduser(), Path(out).expanduser()
    aw, ah = (int(v) for v in aspect.split(":"))
    if aw <= 0 or ah <= 0:
        raise ValueError(f"aspect {aspect!r} needs two positive numbers, e.g. 9:16")
    width, height, duration = await asyncio.to_thread(ffprobe_video, video)
    crop_w = int(height * aw / ah) // 2 * 2
    if crop_w >= width:
        raise ValueError(f"{video.name} is {width}x{height}; it is already at or narrower than {aspect}")
    with tempfile.TemporaryDirectory(prefix="jev-reframe-") as tmp:
        work = Path(tmp)
        frames = await asyncio.to_thread(extract_frames, video, fps, work)
        cuts = await asyncio.to_thread(detect_cuts, video, scene_threshold)
        samples = await locate(frames, target)
        path = plan_path(samples, cuts, duration, width, crop_w)
        await asyncio.to_thread(render, video, out, path, crop_w, height, work)
    confident = [s for s in samples if s.conf is not None and s.conf >= MIN_CONF]
    return {
        "out": str(out),
        "source": f"{width}x{height}",
        "crop": f"{crop_w}x{height}",
        "duration_s": round(duration, 1),
        "frames": len(frames),
        "located": len(samples),
        "confident": len(confident),
        "cuts": [round(c, 2) for c in cuts],
        "path": [(round(t, 2), x) for t, x in path[:: PATH_HZ]],  # one point per second
    }


def reframe(video: str | Path, out: str | Path, **kw) -> dict:
    return asyncio.run(reframe_async(video, out, **kw))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="jev-reframe", description=__doc__.splitlines()[0])
    ap.add_argument("video")
    ap.add_argument("out")
    ap.add_argument("--target", default=DEFAULT_TARGET, help="what the crop should follow")
    ap.add_argument("--fps", type=float, default=3.0, help="frames per second sent to Jev")
    ap.add_argument("--aspect", default="9:16")
    ap.add_argument("--scene-threshold", type=float, default=0.25, help="ffmpeg scene score that counts as a cut")
    a = ap.parse_args(argv)
    try:
        info = reframe(a.video, a.out, target=a.target, fps=a.fps, aspect=a.aspect, scene_threshold=a.scene_threshold)
    except (ValueError, OSError, subprocess.CalledProcessError, JevError) as e:
        sys.exit(f"jev-reframe: {e}")
    info.pop("path")
    json.dump(info, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
