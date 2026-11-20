import { ScreenShell } from "@/components/ScreenShell";
import { useEngineStore } from "@/store/engineStore";

const STATUS_COLOR: Record<string, string> = {
  running: "bg-state-running",
  paused: "bg-state-paused",
  error: "bg-state-error",
  stopped: "bg-state-idle",
};

export function Dashboard() {
  const { connected, status, state, stats } = useEngineStore();

  return (
    <ScreenShell
      title="Dashboard"
      description="Run the session and watch the detectors live."
      milestone="M6"
    >
      <div className="flex flex-col gap-4">
        <div className="flex items-center gap-3 rounded-lg border border-border bg-card p-4">
          <span className={`size-3 rounded-full ${STATUS_COLOR[status] ?? "bg-state-idle"}`} />
          <span className="font-mono text-sm uppercase">{state}</span>
          <span className="ml-auto text-xs text-muted-foreground">
            {connected ? "engine connected" : "waiting for engine"}
          </span>
        </div>
        <dl className="grid grid-cols-3 gap-3">
          {[
            ["Casts", stats.casts],
            ["Catches", stats.catches],
            ["Misses", stats.misses],
          ].map(([label, value]) => (
            <div key={String(label)} className="rounded-lg border border-border bg-card p-4">
              <dt className="text-xs text-muted-foreground">{label}</dt>
              <dd className="text-2xl font-semibold tabular-nums">{value}</dd>
            </div>
          ))}
        </dl>
        <p className="text-sm text-muted-foreground">
          Controls, the live detector chart and the ROI preview land in M6.
        </p>
      </div>
    </ScreenShell>
  );
}
