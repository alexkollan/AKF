import { ScreenShell } from "@/components/ScreenShell";

const STEPS = [
  "Pick the WoW window",
  "Set the search area",
  "Capture the bobber",
  "Record a splash and the background noise",
  "Measure how long the bobber takes to land, and how long a cast lasts",
  "Confirm with five casts",
];

export function Calibration() {
  return (
    <ScreenShell
      title="Calibration"
      description="A guided setup for a new fishing spot. It writes what it measures into the active profile."
    >
      <div className="rounded-lg border border-dashed border-shoal p-6">
        <p className="text-sm">
          The wizard arrives with the next milestone. Until then the defaults are already measured
          from a live session in WoW Forever, and every value can be set by hand in the other
          screens.
        </p>
        <ol className="mt-4 flex list-decimal flex-col gap-1.5 pl-5 text-sm text-mist">
          {STEPS.map((step) => (
            <li key={step}>{step}</li>
          ))}
        </ol>
      </div>
    </ScreenShell>
  );
}
