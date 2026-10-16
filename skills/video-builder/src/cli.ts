/**
 * render: resolved video scenario (JSON) → MP4 via HyperFrames.
 *
 *   node src/cli.ts --spec video.json --theme theme.json [--logo logo.png] [--audio dir/] --out out/
 *
 * Writes out/project/ (the composition), out/video.mp4, out/scene-NN.png (a poster per scene),
 * out/progress.json while rendering and out/report.json at the end. Exit code 0 means the
 * video was written; anything else is an error described on stderr.
 */

import { execFile } from "node:child_process";
import { copyFile, mkdir, readFile, rename, stat, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { join } from "node:path";
import { parseArgs, promisify } from "node:util";

import { createRenderJob, executeRenderJob } from "@hyperframes/producer";

import { AUDIO_FILE, buildComposition, FPS, settledAt, SOUND_LUFS } from "./composition.ts";
import type { Theme, VideoSpec } from "./types.ts";

const run = promisify(execFile);
const require = createRequire(import.meta.url);
const PNG_MAGIC = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
const MAX_LOGO_BYTES = 2 * 1024 * 1024;
const MAX_AUDIO_BYTES = 8 * 1024 * 1024;

/** An MP3 file: an ID3 tag or an MPEG audio frame header at the start. */
function isMp3(data: Buffer): boolean {
  return data.subarray(0, 3).toString("latin1") === "ID3" || (data[0] === 0xff && (data[1]! & 0xe0) === 0xe0);
}

async function hasAudioStream(video: string): Promise<boolean> {
  const { stdout } = await run("ffprobe", ["-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", video]);
  return stdout.trim().length > 0;
}

function parseSpec(value: unknown): VideoSpec {
  const spec = value as Partial<VideoSpec> | null;
  if (!spec || typeof spec.title !== "string" || !Array.isArray(spec.scenes) || !spec.scenes.length) {
    throw new Error("video spec needs a title and at least one scene");
  }
  for (const scene of spec.scenes) {
    if (typeof scene.start !== "number" || typeof scene.duration !== "number" || scene.duration <= 0) {
      throw new Error("every scene needs a start and a positive duration");
    }
  }
  return spec as VideoSpec;
}

async function main(): Promise<void> {
  const { values } = parseArgs({
    options: {
      spec: { type: "string" },
      theme: { type: "string" },
      logo: { type: "string" },
      out: { type: "string" },
      workers: { type: "string" },
      audio: { type: "string" },
    },
  });
  if (!values.spec || !values.theme || !values.out) {
    throw new Error("usage: --spec video.json --theme theme.json [--logo logo.png] --out dir");
  }
  const out = values.out;
  const project = join(out, "project");
  await mkdir(join(project, "assets"), { recursive: true });
  const timings: Record<string, number> = {};
  let started = performance.now();

  const spec = parseSpec(JSON.parse(await readFile(values.spec, "utf8")));
  const theme = JSON.parse(await readFile(values.theme, "utf8")) as Theme;
  let hasLogo = false;
  if (values.logo) {
    const logo = await readFile(values.logo);
    if (!logo.subarray(0, 8).equals(PNG_MAGIC) || logo.length > MAX_LOGO_BYTES) {
      throw new Error("logo must be a PNG up to 2 MB");
    }
    await writeFile(join(project, "assets", "logo.png"), logo);
    hasLogo = true;
  }
  await copyFile(require.resolve("gsap/dist/gsap.min.js"), join(project, "assets", "gsap.min.js"));
  const composition = buildComposition(spec, theme, hasLogo);
  if (composition.audioFiles.length) {
    if (!values.audio) throw new Error("the spec has voice or sounds: pass --audio");
    await mkdir(join(project, "assets", "audio"), { recursive: true });
    for (const name of composition.audioFiles) {
      if (!AUDIO_FILE.test(name)) throw new Error(`unexpected audio file name: ${name}`);
      const data = await readFile(join(values.audio, name));
      if (data.length > MAX_AUDIO_BYTES || !isMp3(data)) throw new Error(`${name} is not an MP3 up to 8 MB`);
      const target = join(project, "assets", "audio", name);
      if (name.startsWith("voice-")) {
        await writeFile(target, data);
      } else {
        // Sound effects come as loud as their generator makes them (an ElevenLabs whoosh peaked
        // at the voice's level): level them first, so the mix volumes mean the same every time.
        const source = join(values.audio, name);
        await run("ffmpeg", ["-v", "error", "-y", "-i", source, "-af", `loudnorm=I=${SOUND_LUFS}:TP=-2:LRA=11`, "-ar", "48000", "-c:a", "libmp3lame", "-b:a", "128k", target]);
      }
    }
  }
  await writeFile(join(project, "index.html"), composition.html);
  timings.compose_ms = performance.now() - started;

  // Progress for the worker, which polls this file while it waits.
  let lastWrite = 0;
  const progress = async (value: number, stage: string, force = false) => {
    const now = performance.now();
    if (!force && now - lastWrite < 1000) return;
    lastWrite = now;
    await writeFile(join(out, "progress.json"), JSON.stringify({ progress: value, stage }));
  };

  started = performance.now();
  const workers = Math.max(1, Math.min(4, Number(values.workers ?? "1") || 1));
  const job = createRenderJob({ fps: FPS, quality: "standard", format: "mp4", workers });
  const video = join(out, "video.mp4");
  await executeRenderJob(job, project, video, (current) => {
    void progress(Math.round(current.progress), "запись видео");
  });
  timings.render_ms = performance.now() - started;

  const audio = composition.audioFiles.length > 0 && (await hasAudioStream(video));
  if (composition.audioFiles.length && !audio) throw new Error("the video came out without its sound");
  if (audio) {
    // Voices differ in loudness (3 dB between two ElevenLabs voices on the same phrase): level
    // the mix to -16 LUFS, the usual target for speech online. The picture is copied as is.
    started = performance.now();
    const levelled = join(out, "video.levelled.mp4");
    await run("ffmpeg", ["-v", "error", "-y", "-i", video, "-map", "0", "-c:v", "copy", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "48000", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", levelled]);
    await rename(levelled, video);
    timings.loudness_ms = performance.now() - started;
  }
  await progress(97, "кадры для предпросмотра", true);

  started = performance.now();
  const posters: string[] = [];
  for (const [i, scene] of spec.scenes.entries()) {
    // Once everything in the scene is on screen, before it fades out.
    const at = settledAt(scene);
    const name = `scene-${String(i + 1).padStart(2, "0")}.png`;
    await run("ffmpeg", ["-v", "error", "-y", "-ss", at.toFixed(3), "-i", video, "-frames:v", "1", "-vf", "scale=960:-2", join(out, name)]);
    posters.push(name);
  }
  timings.posters_ms = performance.now() - started;

  const report = {
    video: "video.mp4",
    posters,
    duration: composition.duration,
    audio,
    frames: job.totalFrames ?? null,
    bytes: (await stat(video)).size,
    timings,
  };
  await writeFile(join(out, "report.json"), `${JSON.stringify(report, null, 2)}\n`);
  console.log(JSON.stringify(report));
}

main().catch((error: unknown) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
});
