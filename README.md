# jev-mcp

MCP server that exposes TypeSafe AI's **Jev** decision model to Claude
(Desktop, Code, Cowork). Three tools:

| Tool | Use |
|---|---|
| `jev_evaluate` | Full System One call: state + any mix of `noul` / `choice` / `score` / `box` questions, optional local images |
| `jev_evaluate_images` | Same questions over many local PNG/JPEG files or a folder (one call per image, 8 in parallel), optional review flag below a confidence threshold |
| `jev_route` | One choice question + confidence threshold → `{choice, confidence, escalate}` |

## Images and box

Pass local paths, never base64: the server reads the file, checks it is PNG or
JPEG (by its bytes, max 16 MB) and sends it as a TypeSafe asset with the
`TypeSafe-Danger-Zone-Images: true` header. Image bytes never pass through the
MCP client's context.

- `jev_evaluate`: `images={"IMG_parcel": "~/photos/p1.jpg"}` and put
  `IMG_parcel` where Jev should see the image (state, instructions or criteria).
  A placeholder that appears nowhere, is shorter than 4 characters, or sits
  inside another placeholder is rejected, so an image is never dropped or
  mangled silently.
- The server reads whatever local path the model names and uploads it to
  TypeSafe (PNG/JPEG only). Fine for a local single-user tool; don't point it
  at folders with private photos you would not send to a third party.
- `box` questions take only `instructions` ("the damaged area", "the person's
  face") and return `{top, bottom, left, right}` as fractions of image height
  and width, plus a confidence. They need an image.
- `jev_evaluate_images`: `paths` takes files and/or folders (a folder adds its
  `*.png/*.jpg/*.jpeg`, sorted, not recursive). With `confidence_threshold`,
  each result lists the question ids answered below it in `review` (noul uses
  `max(p, 1-p)`).
- Large images are fine: anything over 1568 px on its longest side is
  downscaled before upload (needs Pillow, a declared dependency).
  Images with an EXIF orientation tag are sent upright (pixels rotated, tag
  dropped), so box fractions always refer to the image as you see it. A
  re-encoded animated PNG keeps only its first frame.
- Video: Jev has no video input. Pull frames first, e.g.
  `ffmpeg -i in.mp4 -vf fps=1 frames/f_%04d.jpg`, then pass the folder.
  WebP/HEIC: convert first (`sips -s format png in.webp --out out.png`).

## Setup

Needs Python 3.11+ (macOS ships 3.9, so name the version explicitly).

```bash
python3.12 -m venv .venv                  # or: uv venv --python 3.12
.venv/bin/pip install -e ".[dev]"         # or: uv pip install -e ".[dev]"
cp .env.example .env                      # add your JEV_AI_API_KEY
chmod 600 .env                            # it holds a secret
.venv/bin/pytest                          # mocked, no network needed
```

The server reads `JEV_AI_API_KEY` from the environment, falling back to this
`.env`. Keeping the key here means it lives in one file instead of being copied
into every MCP client's config — so the registrations below carry no secrets.

## Try it

```bash
npx @modelcontextprotocol/inspector jev-mcp
```

## Add to Claude Desktop

`claude_desktop_config.json` (Settings → Developer → Edit Config):

```json
{
  "mcpServers": {
    "jev": {
      "command": "/absolute/path/to/jev-mcp/.venv/bin/jev-mcp",
      "args": []
    }
  }
}
```

Fully quit Claude Desktop (⌘Q) and reopen — the config is only read at launch.
Add an `"env": { "JEV_AI_API_KEY": "..." }` block only if you want to override
the key in `.env`; environment variables take precedence.

## Add to Claude Code

```bash
claude mcp add jev -s user -- /absolute/path/to/jev-mcp/.venv/bin/jev-mcp
```

`-s user` makes it available in every project; the default (`local`) registers
it only for the current directory. Restart the session afterwards — MCP servers
are loaded at startup.

## Auto-reframe video (`jev_reframe_video`, `jev-reframe`)

Turns a landscape video into a vertical crop (default 9:16) that follows what
you describe (default: the hands and the tool or material they work on). Needs
`ffmpeg`/`ffprobe` on PATH.

```bash
jev-reframe lesson.mp4 lesson_9x16.mp4 --target "The speaker's face"
```

1. ffmpeg samples frames (default 3 fps) and finds hard cuts (scene score > 0.25).
2. Jev answers one `box` question per frame (8 in parallel).
3. The crop path ignores boxes under 0.15 confidence, interpolates, smooths
   with a 1 s window that never crosses a cut, jumps at cuts, and only moves
   when the subject leaves a dead zone (12 % of the crop width).
4. ffmpeg renders the crop with the original audio.

Measured on a 128 s 1080p sewing lesson (385 frames, ~1 min, a few cents),
against Jev's own boxes (not independent ground truth, so the shipped path is
favoured): the box center was inside the crop for 98.3 % of confident frames,
vs 90.7 % for a first prototype (1 fps, 5 s smoothing, never shipped) and
88.6 % for a static center crop; 100 % in the 3 s after cuts. A visual check
at six timestamps agreed except one (see below).

If more than half of the Jev calls fail (bad key, rejected images, outage)
or none locate anything, the tool returns an error instead of rendering.
A very wide box (two hands far apart plus a tool) centers between them and
can cut off the tool head; narrow the `target` when that matters.

## API reference

Docs: https://docs.typesafe.ai/api — `POST /v1/systemone`, verified 2026-09-21;
images/box contract from `https://api.typesafe.ai/openapi.json` (API 0.2.0), verified 2026-10-30.
Default API base: `https://jev-ai.pro/api` (override with `JEV_AI_API_BASE`).
`https://api.typesafe.ai` accepts our key as of 2026-10-30 (live image calls answered by `jev-1.14.0`).
Jev 1.13 limits: 64k total context, 32k for state + longest question.
Images: PNG/JPEG only (by spec); 16 MB per image is the documented product limit. Longest side max 1568 px (not in the spec; probed 2026-10-30: 1569 returns 400 "Invalid image dimensions"). The server downscales larger images automatically, keeping format and aspect ratio, so box fractions still map onto the original; input ~$0.04 per 1M tokens, output free.
