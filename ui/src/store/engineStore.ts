import { create } from "zustand";

import type {
  AudioDevice,
  BridgeInfo,
  CalibrationStep,
  CatchRecord,
  DetectorLevels,
  EngineState,
  LogLine,
  RunStatus,
  SessionStats,
  ServerEvent,
  WindowItem,
} from "@/lib/protocol";

/** How many samples the live detector chart keeps. At 10 Hz that is one minute. */
const LEVEL_HISTORY = 600;
const LOG_HISTORY = 500;

const emptyStats: SessionStats = {
  startedAt: null,
  elapsedMs: 0,
  casts: 0,
  catches: 0,
  misses: 0,
  consecutiveMisses: 0,
  luresApplied: 0,
  successRate: 0,
  catchesPerHour: 0,
};

interface RoiImage {
  pngBase64: string;
  width: number;
  height: number;
  box: [number, number, number, number] | null;
}

interface ClientShot {
  pngBase64: string;
  width: number;
  height: number;
  clientWidth: number;
  clientHeight: number;
}

export interface CalibrationProgress {
  status: "running" | "done" | "error";
  progress: number;
  message: string;
  result: Record<string, unknown>;
}

interface EngineStore {
  connected: boolean;
  status: RunStatus;
  state: EngineState;
  stopReason: string | null;
  stats: SessionStats;
  levels: DetectorLevels[];
  logs: LogLine[];
  catches: CatchRecord[];
  roi: RoiImage | null;
  profile: Record<string, unknown> | null;
  settings: Record<string, unknown> | null;
  defaults: { profile: Record<string, unknown>; settings: Record<string, unknown> } | null;
  lureRemainingS: number | null;
  audioDevices: AudioDevice[];
  clientShot: ClientShot | null;
  profiles: { names: string[]; active: string };
  bridge: BridgeInfo | null;
  windows: WindowItem[];
  calibration: Partial<Record<CalibrationStep, CalibrationProgress>>;
  lastError: { message: string; command: string | null; at: number } | null;

  setConnected: (connected: boolean) => void;
  applyEvent: (event: ServerEvent) => void;
  reset: () => void;
}

export const useEngineStore = create<EngineStore>((set) => ({
  connected: false,
  status: "stopped",
  state: "IDLE",
  stopReason: null,
  stats: emptyStats,
  levels: [],
  logs: [],
  catches: [],
  roi: null,
  profile: null,
  settings: null,
  defaults: null,
  lureRemainingS: null,
  audioDevices: [],
  clientShot: null,
  profiles: { names: [], active: "" },
  bridge: null,
  windows: [],
  calibration: {},
  lastError: null,

  setConnected: (connected) => set({ connected }),

  applyEvent: (event) =>
    set((prev) => {
      switch (event.type) {
        case "state":
          return { status: event.status, state: event.state, stopReason: event.reason };
        case "stats":
          return { stats: event.stats, lureRemainingS: event.lureRemainingS };
        case "levels":
          return { levels: [...prev.levels, event.levels].slice(-LEVEL_HISTORY) };
        case "log":
          return { logs: [...prev.logs, event.line].slice(-LOG_HISTORY) };
        case "catch":
          return { catches: [...prev.catches, event.record] };
        case "config":
          return { profile: event.profile, settings: event.settings, defaults: event.defaults };
        case "audio_devices":
          return { audioDevices: event.devices };
        case "client_shot":
          return {
            clientShot: {
              pngBase64: event.pngBase64,
              width: event.width,
              height: event.height,
              clientWidth: event.clientWidth,
              clientHeight: event.clientHeight,
            },
          };
        case "bridge": {
          const { type: _type, ...info } = event;
          return { bridge: info };
        }
        case "windows":
          return { windows: event.windows };
        case "calibration":
          return {
            calibration: {
              ...prev.calibration,
              [event.step]: {
                status: event.status,
                progress: event.progress,
                message: event.message,
                result: event.result,
              },
            },
          };
        case "profiles":
          return { profiles: { names: event.names, active: event.active } };
        case "roi":
          return {
            roi: {
              pngBase64: event.pngBase64,
              width: event.width,
              height: event.height,
              box: event.box,
            },
          };
        case "error":
          return {
            lastError: { message: event.message, command: event.command, at: Date.now() },
          };
        default:
          return {};
      }
    }),

  reset: () =>
    set({ stats: emptyStats, levels: [], logs: [], catches: [], stopReason: null }),
}));
