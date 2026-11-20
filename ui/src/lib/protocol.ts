/**
 * Wire contract between the UI and `engine.server` (one WebSocket, JSON frames).
 *
 * This mirrors `engine/protocol.py` field for field; the two change together. Field names
 * are camelCase on the wire, except the `profile` / `settings` payloads, which carry the
 * config models exactly as a profile JSON file stores them (snake_case).
 */

export const ENGINE_STATES = [
  "IDLE",
  "LURE",
  "CAST",
  "LOCATE",
  "WATCH",
  "HOOK",
  "RECOVER",
] as const;

export type EngineState = (typeof ENGINE_STATES)[number];

export type RunStatus = "stopped" | "running" | "paused" | "error";

export interface SessionStats {
  startedAt: number | null;
  elapsedMs: number;
  casts: number;
  catches: number;
  misses: number;
  consecutiveMisses: number;
  luresApplied: number;
  successRate: number;
  catchesPerHour: number;
}

export interface DetectorLevels {
  /** Epoch milliseconds. */
  t: number;
  /** In-band level, noise floor and trigger threshold, all dBFS. */
  audioLevel: number;
  audioFloor: number;
  audioThreshold: number;
  /** Whether the bot was listening for a bite at this moment. */
  armed: boolean;
  /** Null until pixel detection exists. */
  pixelSignal: number | null;
  pixelThreshold: number | null;
}

export interface LogLine {
  /** Epoch milliseconds. */
  t: number;
  level: "debug" | "info" | "warn" | "error";
  message: string;
}

export interface CatchRecord {
  /** Epoch milliseconds. */
  t: number;
  /** Cast to bite. */
  waitMs: number;
  /** Bite heard to hook pressed. */
  reactionMs: number;
}

/** Events the engine pushes to every connected client. */
export type ServerEvent =
  | { type: "state"; status: RunStatus; state: EngineState; reason: string | null }
  | { type: "stats"; stats: SessionStats; lureRemainingS: number | null }
  | { type: "levels"; levels: DetectorLevels }
  | { type: "log"; line: LogLine }
  | { type: "catch"; record: CatchRecord }
  | {
      type: "config";
      profile: Record<string, unknown>;
      settings: Record<string, unknown>;
      /** Factory values, for "Reset to defaults". */
      defaults: { profile: Record<string, unknown>; settings: Record<string, unknown> };
    }
  | { type: "profiles"; names: string[]; active: string }
  | {
      type: "roi";
      pngBase64: string;
      width: number;
      height: number;
      box: [number, number, number, number] | null;
    }
  | { type: "audio_devices"; devices: AudioDevice[] }
  | {
      type: "client_shot";
      pngBase64: string;
      width: number;
      height: number;
      clientWidth: number;
      clientHeight: number;
    }
  | { type: "error"; message: string; command: string | null };

export interface AudioDevice {
  index: number;
  name: string;
  channels: number;
  sampleRate: number;
  isDefault: boolean;
}

/** Commands the UI sends. The global hotkeys call the same engine methods. */
export type ClientCommand =
  | { type: "start" }
  | { type: "pause" }
  | { type: "resume" }
  | { type: "stop" }
  | { type: "emergency_stop" }
  | { type: "apply_lure"; name?: string }
  | { type: "test_cast" }
  /** Deep-merged into the active profile; a running session applies it at the next cast. */
  | { type: "set_config"; patch: Record<string, unknown> }
  /** Deep-merged into the machine-wide settings, which are saved immediately. */
  | { type: "set_settings"; patch: Record<string, unknown> }
  | { type: "load_profile"; name: string }
  /** Without a name, saves the active profile; with one, saves it under that name. */
  | { type: "save_profile"; name?: string }
  | { type: "list_profiles" }
  | { type: "screenshot_roi" }
  /** The whole client, downscaled: canvas for the ROI editor and the eyedropper. */
  | { type: "screenshot_client" }
  /** Replace the active profile with a full profile JSON (from a file) and save it. */
  | { type: "import_profile"; profile: Record<string, unknown> }
  | { type: "delete_profile"; name: string }
  | { type: "list_audio_devices" };
