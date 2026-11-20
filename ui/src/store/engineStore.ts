import { create } from "zustand";

import type {
  CatchRecord,
  DetectorLevels,
  EngineState,
  LogLine,
  RunStatus,
  SessionStats,
  ServerEvent,
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
  successRate: 0,
  catchesPerHour: 0,
};

interface EngineStore {
  connected: boolean;
  status: RunStatus;
  state: EngineState;
  stopReason: string | null;
  stats: SessionStats;
  levels: DetectorLevels[];
  logs: LogLine[];
  catches: CatchRecord[];
  roi: { pngBase64: string; box: [number, number, number, number] | null } | null;

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

  setConnected: (connected) => set({ connected }),

  applyEvent: (event) =>
    set((prev) => {
      switch (event.type) {
        case "state":
          return {
            status: event.status,
            state: event.state,
            stopReason: event.reason ?? null,
          };
        case "stats":
          return { stats: event.stats };
        case "levels":
          return { levels: [...prev.levels, event.levels].slice(-LEVEL_HISTORY) };
        case "log":
          return { logs: [...prev.logs, event.line].slice(-LOG_HISTORY) };
        case "catch":
          return { catches: [...prev.catches, event.record] };
        case "roi":
          return { roi: { pngBase64: event.pngBase64, box: event.box } };
        default:
          return {};
      }
    }),

  reset: () =>
    set({ stats: emptyStats, levels: [], logs: [], catches: [], stopReason: null }),
}));
