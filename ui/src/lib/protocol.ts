/**
 * Wire contract between the UI and `engine.server` (one WebSocket, JSON frames).
 *
 * Kept in one file on purpose: it is the only coupling between the two halves, and the
 * grind bot will reuse it. The engine side is implemented in M5.
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
  successRate: number;
  catchesPerHour: number;
}

export interface DetectorLevels {
  /** Engine clock, milliseconds. */
  t: number;
  audioLevel: number;
  audioThreshold: number;
  pixelSignal: number;
  pixelThreshold: number;
}

export interface LogLine {
  t: number;
  level: "debug" | "info" | "warn" | "error";
  message: string;
}

export interface CatchRecord {
  t: number;
  state: EngineState;
  latencyMs: number | null;
}

/** Events the engine pushes to every connected client. */
export type ServerEvent =
  | { type: "state"; status: RunStatus; state: EngineState; reason?: string }
  | { type: "stats"; stats: SessionStats }
  | { type: "levels"; levels: DetectorLevels }
  | { type: "log"; line: LogLine }
  | { type: "catch"; record: CatchRecord }
  | { type: "config"; profile: unknown; settings: unknown }
  | { type: "roi"; pngBase64: string; box: [number, number, number, number] | null };

/** Commands the UI sends. The global hotkeys call the same handlers. */
export type ClientCommand =
  | { type: "start" }
  | { type: "pause" }
  | { type: "resume" }
  | { type: "stop" }
  | { type: "emergency_stop" }
  | { type: "apply_lure"; name?: string }
  | { type: "test_cast" }
  | { type: "set_config"; patch: Record<string, unknown> }
  | { type: "load_profile"; name: string }
  | { type: "save_profile"; name: string }
  | { type: "list_profiles" }
  | { type: "screenshot_roi" };
