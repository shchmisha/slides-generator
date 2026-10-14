/** A resolved video scenario, as the backend sends it (backend/app/jobs/render.py). */

export type VisualType =
  | "title"
  | "statement"
  | "bullets"
  | "big_number"
  | "comparison"
  | "steps"
  | "closing";

export interface Visual {
  type: VisualType;
  heading?: string;
  items?: string[];
  number?: string;
  caption?: string;
}

/** When each character of the narration is spoken, from the start of its clip (ElevenLabs). */
export interface VoiceAlignment {
  chars: string[];
  starts: number[];
  ends: number[];
}

/** The scene's narration as a voice clip: a file in the --audio folder. */
export interface SceneVoice {
  file: string; // voice-NN.mp3
  offset: number; // seconds from the scene start to the first word
  duration: number;
  alignment?: VoiceAlignment;
}

export interface Scene {
  start: number; // seconds from the beginning of the video
  duration: number;
  narration: string; // shown as captions, and voiced when `voice` is set
  visual: Visual;
  voice?: SceneVoice;
}

export interface Sound {
  file: string; // bed.mp3 | transition.mp3
  seconds: number;
}

/** Optional sound design: a quiet background bed (looped) and a sound at each scene change. */
export interface Sounds {
  bed?: Sound;
  transition?: Sound;
}

export interface VideoSpec {
  title: string;
  scenes: Scene[];
  sounds?: Sounds;
}

export type ColorToken =
  | "primary"
  | "accent"
  | "text"
  | "muted"
  | "positive"
  | "negative"
  | "surface"
  | "grid"
  | "background";

export interface Theme {
  name?: string;
  colors: Record<ColorToken, string>;
  fonts: { heading: string; body: string };
}
