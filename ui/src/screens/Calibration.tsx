import { useState, type ReactNode } from "react";
import { Camera, Check, Crosshair, FlaskConical, Mic, Monitor, Timer } from "lucide-react";

import { RoiEditor } from "@/components/RoiEditor";
import { ScreenShell } from "@/components/ScreenShell";
import { Button } from "@/components/ui/button";
import type { Profile } from "@/lib/config";
import { patchProfile, useProfile } from "@/lib/engine";
import type { CalibrationStep } from "@/lib/protocol";
import { cn } from "@/lib/utils";
import { sendCommand } from "@/lib/ws";
import { useEngineStore, type CalibrationProgress } from "@/store/engineStore";

type StepId = "window" | "area" | "bobber" | "sound" | "timing" | "confirm";

const STEPS: { id: StepId; title: string; icon: typeof Monitor }[] = [
  { id: "window", title: "Pick the WoW window", icon: Monitor },
  { id: "area", title: "Set the search area", icon: Crosshair },
  { id: "bobber", title: "Show the bobber", icon: Camera },
  { id: "sound", title: "Hear the spot", icon: Mic },
  { id: "timing", title: "Time the cast", icon: Timer },
  { id: "confirm", title: "Five test casts", icon: FlaskConical },
];

export function Calibration() {
  const profile = useProfile();
  const [current, setCurrent] = useState(0);
  const [done, setDone] = useState<Partial<Record<StepId, boolean>>>({});
  const markDone = (id: StepId) => setDone((now) => ({ ...now, [id]: true }));

  if (!profile) {
    return <ScreenShell title="Calibration" description="">{null}</ScreenShell>;
  }

  const step = STEPS[current];
  const calibratedAt = profile.calibrated_at ? new Date(profile.calibrated_at) : null;

  return (
    <ScreenShell
      title="Calibration"
      description="Six steps for a new fishing spot. Each one measures something and writes it into the active profile once you accept it."
      actions={
        calibratedAt && (
          <p className="text-sm text-mist">
            {profile.name} calibrated {calibratedAt.toLocaleDateString()}{" "}
            {calibratedAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
          </p>
        )
      }
    >
      <div className="grid grid-cols-[15rem_minmax(0,1fr)] gap-8">
        <ol className="flex flex-col gap-1" aria-label="Calibration steps">
          {STEPS.map((item, index) => {
            const Icon = item.icon;
            return (
              <li key={item.id}>
                <button
                  type="button"
                  onClick={() => setCurrent(index)}
                  aria-current={index === current ? "step" : undefined}
                  className={cn(
                    "flex w-full items-center gap-3 rounded-md px-3 py-2.5 text-left text-sm transition-colors",
                    index === current ? "bg-lagoon/15" : "hover:bg-depth-raised",
                  )}
                >
                  <span
                    className={cn(
                      "flex size-6 shrink-0 items-center justify-center rounded-full border text-xs font-semibold",
                      done[item.id]
                        ? "border-state-running bg-state-running text-abyss"
                        : index === current
                          ? "border-lagoon text-lagoon"
                          : "border-shoal text-mist",
                    )}
                  >
                    {done[item.id] ? <Check className="size-3.5" /> : index + 1}
                  </span>
                  <Icon className="size-4 text-mist" />
                  <span className={index === current ? "text-foam" : "text-mist"}>{item.title}</span>
                </button>
              </li>
            );
          })}
        </ol>

        <div className="flex min-w-0 flex-col gap-5">
          <h2 className="text-lg font-semibold">
            {current + 1}. {step.title}
          </h2>
          {step.id === "window" && <WindowStep profile={profile} onDone={() => markDone("window")} />}
          {step.id === "area" && <AreaStep profile={profile} onDone={() => markDone("area")} />}
          {step.id === "bobber" && <BobberStep onDone={() => markDone("bobber")} />}
          {step.id === "sound" && <SoundStep onDone={() => markDone("sound")} />}
          {step.id === "timing" && <TimingStep profile={profile} onDone={() => markDone("timing")} />}
          {step.id === "confirm" && <ConfirmStep onDone={() => markDone("confirm")} />}

          <div className="mt-2 flex gap-2 border-t border-shoal pt-4">
            <Button variant="ghost" disabled={current === 0} onClick={() => setCurrent(current - 1)}>
              Back
            </Button>
            {current < STEPS.length - 1 && (
              <Button variant="outline" className="ml-auto" onClick={() => setCurrent(current + 1)}>
                {done[step.id] ? "Next step" : "Skip this step"}
              </Button>
            )}
          </div>
        </div>
      </div>
    </ScreenShell>
  );
}

/* ------------------------------------------------------------------------------------ */

function useStep(step: CalibrationStep): CalibrationProgress | undefined {
  return useEngineStore((store) => store.calibration[step]);
}

function Instructions({ children }: { children: ReactNode }) {
  return <div className="max-w-prose text-sm leading-relaxed text-mist">{children}</div>;
}

function Progress({ progress, label }: { progress: CalibrationProgress | undefined; label: string }) {
  if (!progress) {
    return null;
  }
  if (progress.status === "error") {
    return <p className="text-sm text-state-error">{progress.message || "That step failed."}</p>;
  }
  return (
    <div className="flex flex-col gap-1.5">
      <div className="h-1.5 w-full max-w-md overflow-hidden rounded-full bg-depth-raised">
        <div
          className="h-full bg-lagoon transition-[width] duration-300"
          style={{ width: `${Math.round(progress.progress * 100)}%` }}
        />
      </div>
      <p className="text-xs text-mist">{progress.status === "running" ? progress.message || label : "Done."}</p>
    </div>
  );
}

function Result({ children }: { children: ReactNode }) {
  return <div className="rounded-lg border border-shoal bg-depth p-4 text-sm">{children}</div>;
}

/* ------------------------------------------------------------------------------------ */

function WindowStep({ profile, onDone }: { profile: Profile; onDone: () => void }) {
  const allWindows = useEngineStore((store) => store.windows);
  const [chosen, setChosen] = useState<number | null>(null);
  const [showAll, setShowAll] = useState(false);
  const picked = profile.fingerprint.client_width > 0;
  // Only the game, when it is found; everything else is a click away.
  const games = allWindows.filter((item) => item.likelyGame);
  const windows = showAll || games.length === 0 ? allWindows : games;

  return (
    <>
      <Instructions>
        Have WoW open, then look for windows and pick the game. The profile remembers which
        program it is and what size the window was, so it can tell when that changes.
      </Instructions>
      <div className="flex items-center gap-3">
        <Button variant="outline" onClick={() => sendCommand({ type: "list_windows" })}>
          Look for windows
        </Button>
        {games.length > 0 && allWindows.length > games.length && (
          <Button variant="ghost" size="sm" onClick={() => setShowAll((now) => !now)}>
            {showAll ? "Show WoW only" : `Show all ${allWindows.length} windows`}
          </Button>
        )}
      </div>
      {windows.length > 0 && (
        <ul role="radiogroup" aria-label="Open windows" className="flex flex-col divide-y divide-shoal/70 rounded-lg border border-shoal">
          {windows.map((item) => (
            <li key={item.hwnd}>
              <button
                type="button"
                role="radio"
                aria-checked={chosen === item.hwnd}
                onClick={() => setChosen(item.hwnd)}
                className={cn(
                  "flex w-full items-center gap-3 px-4 py-2.5 text-left text-sm",
                  chosen === item.hwnd ? "bg-lagoon/10" : "hover:bg-depth-raised",
                )}
              >
                <span className="min-w-0 flex-1 truncate">
                  <span className="font-semibold">{item.process}</span>
                  <span className="ml-2 text-mist">{item.title}</span>
                </span>
                <span className="shrink-0 tabular-nums text-mist">
                  {item.width}×{item.height}, screen {item.monitor + 1}
                </span>
                {item.likelyGame && <span className="rounded bg-lagoon/15 px-1.5 text-xs text-lagoon">WoW</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="flex items-center gap-3">
        <Button
          disabled={chosen === null}
          onClick={() => {
            if (chosen !== null) {
              sendCommand({ type: "select_window", hwnd: chosen });
              onDone();
            }
          }}
        >
          Use this window
        </Button>
        {picked && (
          <span className="text-sm text-mist">
            Using {profile.capture.process_name}, {profile.fingerprint.client_width}×
            {profile.fingerprint.client_height}
          </span>
        )}
      </div>
    </>
  );
}

function AreaStep({ profile, onDone }: { profile: Profile; onDone: () => void }) {
  const shot = useEngineStore((store) => store.clientShot);
  return (
    <>
      <Instructions>
        Cast once by hand so you can see where the bobber lands, take a snapshot, and drag
        across the water around it. Leave room on every side: the bobber does not land in the
        same place twice. Sound detection does not use this; mode B and picture detection do.
      </Instructions>
      <div className="flex gap-2">
        <Button variant="outline" onClick={() => sendCommand({ type: "screenshot_client" })}>
          <Camera />
          {shot ? "Take a new snapshot" : "Take a snapshot"}
        </Button>
        <Button variant="outline" onClick={onDone}>
          Keep this area
        </Button>
      </div>
      {shot && (
        <RoiEditor
          pngBase64={shot.pngBase64}
          roi={profile.detection.search_roi}
          onRoi={(search_roi) => {
            patchProfile({ detection: { search_roi } });
            onDone();
          }}
        />
      )}
    </>
  );
}

function BobberStep({ onDone }: { onDone: () => void }) {
  const shot = useEngineStore((store) => store.clientShot);
  const progress = useStep("bobber");
  const [mark, setMark] = useState<{ x: number; y: number } | null>(null);

  return (
    <>
      <Instructions>
        Cast by hand and, while the bobber floats, take a snapshot and click the middle of the
        bobber. Its picture and its feather colour are saved with the profile. Modes A and C
        listen instead of looking, so they can skip this.
      </Instructions>
      <div>
        <Button variant="outline" onClick={() => sendCommand({ type: "screenshot_client" })}>
          <Camera />
          {shot ? "Take a new snapshot" : "Take a snapshot"}
        </Button>
      </div>
      {shot && (
        <div
          className="relative cursor-crosshair overflow-hidden rounded-md border border-shoal"
          onClick={(event) => {
            const box = event.currentTarget.getBoundingClientRect();
            const point = {
              x: (event.clientX - box.left) / box.width,
              y: (event.clientY - box.top) / box.height,
            };
            setMark(point);
            sendCommand({ type: "capture_bobber", ...point });
          }}
        >
          <img alt="The WoW window" draggable={false} src={`data:image/png;base64,${shot.pngBase64}`} className="block w-full" />
          {mark && (
            <span
              className="pointer-events-none absolute size-6 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-lagoon"
              style={{ left: `${mark.x * 100}%`, top: `${mark.y * 100}%` }}
            />
          )}
        </div>
      )}
      {progress?.status === "error" && <p className="text-sm text-state-error">{progress.message}</p>}
      {progress?.status === "done" && (
        <Result>
          Bobber saved. The feather colour range is set from it.
          <Button size="sm" variant="outline" className="ml-3" onClick={onDone}>
            Looks right
          </Button>
        </Result>
      )}
    </>
  );
}

function SoundStep({ onDone }: { onDone: () => void }) {
  const progress = useStep("sound");
  const running = progress?.status === "running";
  const result = progress?.status === "done" ? progress.result : null;

  return (
    <>
      <Instructions>
        Stand at the water with WoW in front and its sound on, and do not cast for twenty
        seconds. The bot listens to the waves and wind of this spot and works out how loud a
        splash must be to count. Music should be off; other apps do not matter.
      </Instructions>
      <div className="flex gap-2">
        <Button disabled={running} onClick={() => sendCommand({ type: "calibrate_sound", seconds: 20 })}>
          <Mic />
          Listen for 20 seconds
        </Button>
        {running && (
          <Button variant="ghost" onClick={() => sendCommand({ type: "cancel_calibration" })}>
            Cancel
          </Button>
        )}
      </div>
      <Progress progress={progress} label="Listening…" />
      {result && (
        <Result>
          <p>
            Background sound sits at <strong>{String(result.floorDb)} dB</strong>; its loudest
            moments rise <strong>+{String(result.peakOverDb)} dB</strong> above that.
          </p>
          <p className="mt-1 text-mist">
            Suggested sensitivity: a splash must rise <strong className="text-foam">+{String(result.suggestedOverDb)} dB</strong>.
          </p>
          <Button
            size="sm"
            className="mt-3"
            onClick={() => {
              patchProfile({ detection: { audio: { trigger_ratio: Number(result.suggestedRatio) } } });
              onDone();
            }}
          >
            Use +{String(result.suggestedOverDb)} dB
          </Button>
        </Result>
      )}
    </>
  );
}

function TimingStep({ profile, onDone }: { profile: Profile; onDone: () => void }) {
  const progress = useStep("timing");
  const running = progress?.status === "running";
  const result = progress?.status === "done" ? progress.result : null;
  const landings = (result?.landingsS as number[] | undefined) ?? [];
  const landDelay = result?.suggestedLandDelayMs as number | null | undefined;
  const maxWait = result?.suggestedMaxWaitMs as number | null | undefined;

  return (
    <>
      <Instructions>
        The bot casts three times and reels straight back in, timing how long the bobber takes
        to land. With the FishBridge addon it also reads how long a cast can last. WoW comes to
        the front; keep your hands off the keyboard until it is done.
        {profile.mode === "B" && " This step needs mode A or C."}
      </Instructions>
      <div className="flex gap-2">
        <Button disabled={running || profile.mode === "B"} onClick={() => sendCommand({ type: "measure_timing", casts: 3 })}>
          <Timer />
          Cast three times
        </Button>
        {running && (
          <Button variant="ghost" onClick={() => sendCommand({ type: "cancel_calibration" })}>
            Cancel
          </Button>
        )}
      </div>
      <Progress progress={progress} label="Casting…" />
      {result && (
        <Result>
          {landings.length ? (
            <p>
              The bobber landed after {landings.map((value) => `${value.toFixed(1)} s`).join(", ")}.
              {landDelay && ` Listen from ${(landDelay / 1000).toFixed(1)} s after the cast.`}
            </p>
          ) : (
            <p className="text-state-paused">
              {result.dryRun
                ? "Practice mode is on, so no casts were made. Turn it off in Settings to measure."
                : "No landing splash was heard. Check that WoW's sound is on, then try again."}
            </p>
          )}
          {maxWait ? (
            <p className="mt-1 text-mist">A cast lasts {(maxWait / 1000).toFixed(0)} s (from FishBridge).</p>
          ) : (
            <p className="mt-1 text-mist">Without FishBridge the bot keeps waiting {profile.timing.max_wait_ms / 1000} s for a bite.</p>
          )}
          {(landDelay || maxWait) && (
            <Button
              size="sm"
              className="mt-3"
              onClick={() => {
                patchProfile({
                  timing: {
                    ...(landDelay ? { land_delay_ms: landDelay } : {}),
                    ...(maxWait ? { max_wait_ms: maxWait } : {}),
                  },
                });
                onDone();
              }}
            >
              Use these timings
            </Button>
          )}
        </Result>
      )}
    </>
  );
}

function ConfirmStep({ onDone }: { onDone: () => void }) {
  const status = useEngineStore((store) => store.status);
  const reason = useEngineStore((store) => store.stopReason);
  const stats = useEngineStore((store) => store.stats);
  const finished = useStep("finish");
  const [started, setStarted] = useState(false);
  const testing = started && (status === "running" || status === "paused");
  const outcome = started && !testing && reason?.startsWith("test done") ? reason : null;

  return (
    <>
      <Instructions>
        Five real casts with everything you set, then the bot stops and tells you how many
        it hooked. Four or five out of five is a good spot. Fewer usually means the sound step
        needs another go.
      </Instructions>
      <div className="flex gap-2">
        <Button
          disabled={testing}
          onClick={() => {
            setStarted(true);
            sendCommand({ type: "test_cast", count: 5 });
          }}
        >
          <FlaskConical />
          Run five casts
        </Button>
      </div>
      {testing && (
        <p className="text-sm text-mist">
          Fishing… {stats.catches} hooked, {stats.misses} without a bite so far.
        </p>
      )}
      {outcome && (
        <Result>
          <p className="font-semibold">{outcome.replace("test done: ", "").replace(/^./, (c) => c.toUpperCase())}.</p>
          <Button
            size="sm"
            className="mt-3"
            onClick={() => {
              sendCommand({ type: "finish_calibration" });
              onDone();
            }}
          >
            Finish and save the profile
          </Button>
        </Result>
      )}
      {finished?.status === "done" && <p className="text-sm text-state-running">Calibration saved with the profile.</p>}
    </>
  );
}
