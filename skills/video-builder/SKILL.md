---
name: video-builder
description: Record an MP4 explainer from a resolved video scenario with HyperFrames, using fixed scene templates (title, statement, bullets, big number, comparison, steps, closing), on-screen captions, optional voice-over clips and sounds, and a poster per scene. Use after the user has approved the video scenario.
---

# video-builder

Turns a **resolved video scenario** into `video.mp4`. Each scene becomes a timed clip in a
[HyperFrames](https://github.com/heygen-com/hyperframes) composition: our own HTML templates, one
paused GSAP timeline, captions on their own track. HyperFrames seeks that timeline frame by frame
in headless Chromium and FFmpeg encodes the frames, so the same input always gives the same video.

## When to use

After the video scenario is approved. The scenario only chooses a visual type and its texts;
**it never contains HTML, CSS or script**. Texts are escaped into fixed markup, and the page has a
Content-Security-Policy that blocks every request outside its own folder.

## Run

```bash
node src/cli.ts --spec video.json --theme theme.json [--logo logo.png] [--audio audio/] --out out/ [--workers 1]
```

Outputs in `out/`:

- `project/` — the composition (`index.html`, `assets/gsap.min.js`, `assets/logo.png`)
- `video.mp4` — 1920×1080, 30 fps, H.264; with voice or sounds also AAC audio levelled to −16 LUFS
- `scene-NN.png` — one poster per scene, taken when everything in the scene is on screen
- `progress.json` — `{"progress": 0–100, "stage": "…"}`, rewritten about once a second
- `report.json`:

```json
{ "video": "video.mp4", "posters": ["scene-01.png"], "duration": 72, "audio": false, "frames": 2160,
  "bytes": 3400000, "timings": { "compose_ms": 12, "render_ms": 95000, "posters_ms": 900 } }
```

Exit code 0 means the video was written; anything else is an error described on stderr.

## Video spec

```json
{ "title": "…", "scenes": [ { "start": 0, "duration": 12, "narration": "…", "visual": { … } } ] }
```

`start` is the scene's place on the whole video (the backend adds up the durations).
`narration` is shown as captions, split into short sentences and timed by their length.

| `visual.type` | Fields                             | Shows                                         |
|---------------|------------------------------------|-----------------------------------------------|
| `title`       | `heading`, `caption?`              | Large heading with an accent bar              |
| `statement`   | `heading`, `caption?`              | One idea in a sentence                        |
| `bullets`     | `heading?`, `items` (≤ 5)          | Points appearing one by one                   |
| `big_number`  | `number`, `caption?`               | One figure, exactly as in the source          |
| `comparison`  | `heading?`, `items` (2–4)          | Cards side by side, the first highlighted     |
| `steps`       | `heading?`, `items` (≤ 5)          | Numbered steps in a row                       |
| `closing`     | `heading`, `caption?`              | Final message                                 |

Points of a scene appear over its first half, as the narrator gets to them.

### Voice and sounds (optional)

```json
{ "scenes": [ { "…": "…", "voice": { "file": "voice-01.mp3", "offset": 0.35, "duration": 9.4,
                  "alignment": { "chars": ["С", "у"], "starts": [0, 0.06], "ends": [0.06, 0.12] } } } ],
  "sounds": { "bed": { "file": "bed.mp3", "seconds": 20 },
              "transition": { "file": "transition.mp3", "seconds": 1 } } }
```

Files come from the `--audio` folder; their names must be `voice-NN.mp3`, `bed.mp3` or
`transition.mp3` (MP3, up to 8 MB). A voiced scene's clip starts `offset` seconds into the scene;
with `alignment` (character timing from ElevenLabs) each caption appears as its first word is
spoken, without it captions share the clip's length. The bed loops under the whole video at a
low level with fades at both ends; the transition cue plays at each scene change. Without any of
these the video has no sound track.

## Theme

The same theme JSON as `pptx-builder`. The background is the brand's primary colour; text is
white or the theme's text colour, whichever reads better; the accent colour is used only when it
stands out from the background. Brand fonts get the same metric-compatible substitutes as decks
(`../pptx-builder/fonts/substitutes.json`).

## Environment

- `PRODUCER_HEADLESS_SHELL_PATH` — Chromium's headless shell (on Linux HyperFrames captures with
  BeginFrame; elsewhere with screenshots, which is slower)
- `ffmpeg` and `ffprobe` on `PATH`

## Tests

```bash
npm test          # composition timing, markup, escaping, captions, palette
npm run typecheck
```
