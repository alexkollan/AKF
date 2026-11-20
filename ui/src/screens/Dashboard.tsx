import { useEffect, useRef, useState } from "react";
import { Camera, Pause, Play, Square } from "lucide-react";

import { keyLabel } from "@/components/KeyCapture";
import { WaterLine } from "@/components/WaterLine";
import { Button } from "@/components/ui/button";
import { useProfile, useSettings } from "@/lib/engine";
import type { EngineState, RunStatus } from "@/lib/protocol";
import { cn } from "@/lib/utils";
import { sendCommand } from "@/lib/ws";
import { useEngineStore } from "@/store/engineStore";

/** What each state means to someone watching, not what the code calls it. */
const STATE_WORDS: Record<EngineState, string> = {
  IDLE: "Idle",
  LURE: "Applying a lure",
  CAST: "Casting",
  LOCATE: "Finding the bobber",
  WATCH: "Listening for a bite",
  HOOK: "Hooking",
  RECOVER: "Between casts",
};

const STATUS_COLOR: Record<RunStatus, string> = {
  running: "bg-state-running",
  paused: "bg-state-paused",
  error: "bg-state-error",
  stopped: "bg-state-idle",
};

const STATUS_TEXT: Record<RunStatus, string> = {
  running: "text-state-running",
  paused: "text-state-paused",
  error: "text-state-error",
  stopped: "text-mist",
};

/** Reasons the user caused; anything else is the bot stopping itself and deserves a banner. */
const USER_REASONS = new Set(["stopped by user", "paused by user"]);
const isUserReason = (reason: string) => USER_REASONS.has(reason) || reason.startsWith("test done");

interface DashboardProps {
  openScreen: (id: string) => void;
}

export function Dashboard({ openScreen }: DashboardProps) {
  const { status, state, stopReason, connected } = useEngineStore();

  return (
    // A column, not a grid: the banner comes and goes, and a grid's row tracks would shift.
    <section className="flex h-full flex-col gap-5 p-6">
      <Header status={status} state={state} connected={connected} />
      {stopReason && !isUserReason(stopReason) && status !== "running" && (
        <StopBanner status={status} reason={stopReason} openScreen={openScreen} />
      )}
      <div className="grid min-h-0 flex-1 grid-rows-[minmax(220px,45%)_minmax(0,1fr)] gap-5">
        <Listening />
        <div className="grid min-h-0 grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)] gap-5">
          <Session />
          <EventLog />
        </div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------------------------ */

function Header({
  status,
  state,
  connected,
}: {
  status: RunStatus;
  state: EngineState;
  connected: boolean;
}) {
  const settings = useSettings();
  const running = status === "running";
  const paused = status === "paused";
  const active = running || paused;

  return (
    <header className="flex flex-wrap items-center gap-x-8 gap-y-4">
      <div className="flex min-w-0 items-center gap-4">
        <span
          className={cn(
            "size-4 shrink-0 rounded-full",
            STATUS_COLOR[status],
            running && "shadow-[0_0_0_6px_color-mix(in_oklab,var(--state-running)_18%,transparent)]",
          )}
          aria-hidden
        />
        <div className="min-w-0">
          <p className="text-[34px] leading-none font-semibold tracking-tight [font-stretch:118%]">
            {active ? STATE_WORDS[state] : status === "error" ? "Something went wrong" : "Ready to fish"}
          </p>
          <p className={cn("mt-1.5 text-sm", STATUS_TEXT[status])}>
            {!connected
              ? "The engine is not running — start it with `uv run python -m engine.server`."
              : status === "running"
                ? "Running"
                : status === "paused"
                  ? "Paused"
                  : status === "error"
                    ? "Stopped with an error"
                    : "Stopped"}
            {settings?.dry_run && connected && (
              <span className="ml-2 text-state-paused">(practice mode: no keys are pressed)</span>
            )}
          </p>
        </div>
      </div>

      <div className="ml-auto flex items-center gap-3">
        {!active && (
          <Button
            size="lg"
            className="h-12 px-6 text-base"
            disabled={!connected}
            onClick={() => sendCommand({ type: "start" })}
          >
            <Play />
            Start fishing
          </Button>
        )}
        {active && (
          <Button
            size="lg"
            variant="outline"
            className={cn("h-12 px-5 text-base", paused && "border-state-paused text-state-paused")}
            onClick={() => sendCommand({ type: paused ? "resume" : "pause" })}
          >
            {paused ? <Play /> : <Pause />}
            {paused ? "Resume" : "Pause"}
          </Button>
        )}
        {active && (
          <Button
            size="lg"
            variant="destructive"
            className="h-12 px-5 text-base"
            onClick={() => sendCommand({ type: "stop" })}
          >
            <Square />
            Stop
          </Button>
        )}
      </div>

      {settings && (
        <p className="basis-full text-xs text-mist">
          Works from inside WoW too: <Kbd>{settings.hotkeys.start_stop}</Kbd> start or stop,{" "}
          <Kbd>{settings.hotkeys.pause}</Kbd> pause, <Kbd>{settings.hotkeys.emergency_stop}</Kbd>{" "}
          emergency stop. Clicking out of WoW pauses the bot.
        </p>
      )}
    </header>
  );
}

function Kbd({ children }: { children: string }) {
  return (
    <kbd className="rounded border border-b-2 border-shoal bg-depth-raised px-1.5 py-px font-sans text-[11px] text-foam">
      {keyLabel(children)}
    </kbd>
  );
}

function StopBanner({
  status,
  reason,
  openScreen,
}: {
  status: RunStatus;
  reason: string;
  openScreen: (id: string) => void;
}) {
  const aboutDetection = /miss|bite|bobber/i.test(reason);
  return (
    <div
      role="status"
      className={cn(
        "flex items-center gap-4 rounded-lg border px-4 py-3",
        status === "paused" ? "border-state-paused/50 bg-state-paused/10" : "border-state-error/50 bg-state-error/10",
      )}
    >
      <p className="text-sm">
        <span className="font-semibold">{reason}</span>
        {status === "paused" && <span className="text-mist"> — press Resume when you are ready.</span>}
      </p>
      {aboutDetection && (
        <Button variant="outline" size="sm" className="ml-auto" onClick={() => openScreen("detection")}>
          Open Detection
        </Button>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------------------------ */

function Listening() {
  const levels = useEngineStore((store) => store.levels);
  const catches = useEngineStore((store) => store.catches);
  const box = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState(240);

  useEffect(() => {
    if (!box.current) {
      return;
    }
    const observer = new ResizeObserver(([entry]) => setHeight(Math.max(160, entry.contentRect.height - 32)));
    observer.observe(box.current);
    return () => observer.disconnect();
  }, []);

  const last = levels[levels.length - 1];
  return (
    <div ref={box} className="flex min-h-0 flex-col">
      <div className="mb-2 flex items-baseline gap-4 text-sm">
        <h2 className="font-semibold">What the bot hears</h2>
        <span className="text-mist">
          A bite is a splash that breaks the bright line while the water is lit.
        </span>
        {last && (
          <span className="ml-auto tabular-nums text-mist">
            {last.audioLevel.toFixed(0)} dB now, line at {last.audioThreshold.toFixed(0)} dB
          </span>
        )}
      </div>
      <WaterLine levels={levels} catches={catches} height={height} />
    </div>
  );
}

/* ------------------------------------------------------------------------------------ */

function formatDuration(ms: number): string {
  const total = Math.floor(ms / 1000);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  const mm = String(minutes).padStart(2, "0");
  const ss = String(seconds).padStart(2, "0");
  return hours ? `${hours}:${mm}:${ss}` : `${mm}:${ss}`;
}

function Session() {
  const stats = useEngineStore((store) => store.stats);
  const lure = useEngineStore((store) => store.lureRemainingS);
  const roi = useEngineStore((store) => store.roi);
  const profile = useProfile();
  const lureName = profile?.lures.lures.filter((item) => item.enabled).sort((a, b) => b.priority - a.priority)[0]?.name;

  return (
    <div className="flex min-h-0 flex-col gap-5 rounded-lg bg-depth p-5">
      <div className="flex items-end gap-6">
        <div>
          <p className="text-[64px] leading-[0.85] font-semibold tabular-nums [font-stretch:72%]">
            {stats.catches}
          </p>
          <p className="mt-2 text-sm text-mist">fish hooked</p>
        </div>
        <dl className="grid flex-1 grid-cols-3 gap-x-5 gap-y-3 border-l border-shoal pl-6 text-sm">
          <Stat label="Session" value={formatDuration(stats.elapsedMs)} />
          <Stat label="Casts" value={String(stats.casts)} />
          <Stat label="No bite" value={String(stats.misses)} />
          <Stat label="Success" value={`${Math.round(stats.successRate * 100)}%`} />
          <Stat label="Per hour" value={stats.catchesPerHour.toFixed(0)} />
          <Stat
            label={lureName ? `${lureName}` : "Lure"}
            value={lure === null ? "off" : formatDuration(lure * 1000)}
          />
        </dl>
      </div>

      <BridgeStatus />

      <div className="flex min-h-0 flex-1 gap-4 border-t border-shoal pt-4">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold">Search area</p>
          <p className="mt-1 text-xs text-mist">
            The part of the screen the bot looks at. Mode A listens only, so this matters for
            mode B and pixel detection.
          </p>
          <Button
            variant="outline"
            size="sm"
            className="mt-3"
            onClick={() => sendCommand({ type: "screenshot_roi" })}
          >
            <Camera />
            Take a snapshot
          </Button>
        </div>
        <div className="flex aspect-video h-full max-h-36 min-h-0 items-center justify-center overflow-hidden rounded-md border border-shoal bg-abyss">
          {roi ? (
            <img
              alt="Snapshot of the search area"
              src={`data:image/png;base64,${roi.pngBase64}`}
              className="h-full w-full object-cover"
            />
          ) : (
            <span className="px-3 text-center text-xs text-mist">No snapshot yet</span>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * What the FishBridge addon says, as a row of short facts. Only shown during a session,
 * since that is when the engine reads it; without the addon it says what it would add.
 */
function BridgeStatus() {
  const bridge = useEngineStore((store) => store.bridge);
  const active = useEngineStore((store) => store.status === "running" || store.status === "paused");
  if (!active || bridge === null) {
    return null;
  }
  if (!bridge.present) {
    return (
      <p className="-mt-2 text-xs text-mist">
        FishBridge addon not seen. With it the bot also stops when your bags are full and pauses
        when something attacks you.
      </p>
    );
  }

  const facts: { text: string; tone: "ok" | "warn" | "bad" }[] = [];
  if (bridge.freeSlots !== null && bridge.totalSlots !== null) {
    facts.push({
      text: `${bridge.freeSlots} of ${bridge.totalSlots} bag slots free`,
      tone: bridge.freeSlots === 0 ? "bad" : bridge.freeSlots < 5 ? "warn" : "ok",
    });
  }
  if (bridge.inCombat !== null) {
    facts.push(bridge.inCombat ? { text: "In combat", tone: "bad" } : { text: "Not in combat", tone: "ok" });
  }
  if (bridge.poleEquipped !== null) {
    facts.push(
      bridge.poleEquipped
        ? { text: "Fishing pole equipped", tone: "ok" }
        : { text: "No fishing pole equipped", tone: "warn" },
    );
  }
  if (bridge.lureActive !== null) {
    facts.push(
      bridge.lureActive && bridge.lureRemainingS
        ? { text: `Lure on, ${Math.ceil(bridge.lureRemainingS / 60)} min left`, tone: "ok" }
        : { text: "No lure on", tone: "ok" },
    );
  }

  const toneClass = { ok: "text-foam", warn: "text-state-paused", bad: "text-state-error" };
  return (
    <ul className="-mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs" aria-label="FishBridge addon">
      {facts.map((fact) => (
        <li key={fact.text} className={toneClass[fact.tone]}>
          {fact.text}
        </li>
      ))}
    </ul>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="truncate text-mist">{label}</dt>
      <dd className="text-xl font-semibold tabular-nums [font-stretch:85%]">{value}</dd>
    </div>
  );
}

/* ------------------------------------------------------------------------------------ */

const LEVEL_COLOR = {
  debug: "text-mist",
  info: "text-foam",
  warn: "text-state-paused",
  error: "text-state-error",
} as const;

function EventLog() {
  const logs = useEngineStore((store) => store.logs);
  const list = useRef<HTMLOListElement>(null);
  // Scroll the list itself. scrollIntoView would also scroll every ancestor, and it now
  // returns a Promise in Chromium — which an expression-bodied effect hands to React as a
  // "cleanup function", crashing the whole tree on unmount.
  useEffect(() => {
    if (list.current) {
      list.current.scrollTop = list.current.scrollHeight;
    }
  }, [logs.length]);

  return (
    <div className="flex min-h-0 flex-col rounded-lg bg-depth p-5">
      <h2 className="mb-3 text-sm font-semibold">What happened</h2>
      {logs.length === 0 ? (
        <p className="text-sm text-mist">Casts, bites and hooks show up here as they happen.</p>
      ) : (
        <ol ref={list} className="min-h-0 flex-1 overflow-y-auto pr-2 text-sm">
          {logs.slice(-200).map((line, index) => (
            <li key={`${line.t}-${index}`} className="flex gap-3 py-0.5">
              <time className="shrink-0 tabular-nums text-mist">
                {new Date(line.t).toLocaleTimeString([], { hour12: false })}
              </time>
              <span className={LEVEL_COLOR[line.level]}>{line.message}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
