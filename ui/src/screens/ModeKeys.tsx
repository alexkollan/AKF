import { Check } from "lucide-react";

import { KeyCapture } from "@/components/KeyCapture";
import { ScreenShell } from "@/components/ScreenShell";
import { Field, NumberInput, Section, Toggle, differs } from "@/components/fields";
import type { Mode, Profile } from "@/lib/config";
import { patchProfile, useDefaults, useProfile } from "@/lib/engine";
import { cn } from "@/lib/utils";

interface ModeInfo {
  mode: Mode;
  title: string;
  summary: string;
  needs: string[];
  available: boolean;
}

const MODES: ModeInfo[] = [
  {
    mode: "A",
    title: "One-button addon",
    summary: "Press the addon's key to cast, press it again when a fish bites. The addon does the rest.",
    needs: [
      "Your one-button fishing addon is enabled",
      "Its key is bound below",
      "Auto Loot is on",
    ],
    available: true,
  },
  {
    mode: "B",
    title: "Right-click the bobber",
    summary: "Cast with a key, find the bobber on screen and right-click it when a fish bites.",
    needs: [
      "Auto Loot is on, or Shift+right-click is enabled below",
      "The bobber is visible in the search area",
      "Bobber vision, which is not built yet",
    ],
    available: false,
  },
  {
    mode: "C",
    title: "Interact key, no addon",
    summary: "Cast with a key, then use WoW's soft-target interact key to reel in.",
    needs: [
      "Interact Key is enabled in WoW's options",
      "/console SoftTargetInteractRange 30",
      "No target selected while fishing",
    ],
    available: true,
  },
];

export function ModeKeys() {
  const profile = useProfile();
  const defaults = useDefaults()?.profile;

  return (
    <ScreenShell
      title="Mode & keys"
      description="How the bot casts and reels in, and which keys it presses in WoW."
    >
      {profile && defaults && (
        <>
          <ModeSection profile={profile} defaults={defaults} />
          <KeysSection profile={profile} defaults={defaults} />
          <TimingSection profile={profile} defaults={defaults} />
          {profile.mode === "B" && <MouseSection profile={profile} defaults={defaults} />}
        </>
      )}
    </ScreenShell>
  );
}

interface Props {
  profile: Profile;
  defaults: Profile;
}

function ModeSection({ profile, defaults }: Props) {
  return (
    <Section
      title="Fishing mode"
      onReset={() => patchProfile({ mode: defaults.mode })}
    >
      <div role="radiogroup" aria-label="Fishing mode" className="flex flex-col gap-2">
        {MODES.map((info) => {
          const selected = profile.mode === info.mode;
          return (
            <button
              key={info.mode}
              type="button"
              role="radio"
              aria-checked={selected}
              onClick={() => patchProfile({ mode: info.mode })}
              className={cn(
                "flex gap-4 rounded-lg border p-4 text-left transition-colors",
                selected ? "border-lagoon bg-lagoon/10" : "border-shoal hover:border-mist",
              )}
            >
              <span
                className={cn(
                  "mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full border text-sm font-semibold",
                  selected ? "border-lagoon bg-lagoon text-primary-foreground" : "border-shoal text-mist",
                )}
              >
                {info.mode}
              </span>
              <span className="min-w-0">
                <span className="flex items-center gap-2 font-semibold">
                  {info.title}
                  {!info.available && (
                    <span className="rounded bg-state-paused/15 px-1.5 py-0.5 text-xs font-normal text-state-paused">
                      not available yet
                    </span>
                  )}
                </span>
                <span className="mt-1 block text-sm text-mist">{info.summary}</span>
                {selected && (
                  <ul className="mt-3 flex flex-col gap-1 text-sm">
                    {info.needs.map((need) => (
                      <li key={need} className="flex items-start gap-2">
                        <Check className="mt-0.5 size-4 shrink-0 text-lagoon" />
                        {need}
                      </li>
                    ))}
                  </ul>
                )}
              </span>
            </button>
          );
        })}
      </div>
    </Section>
  );
}

function KeysSection({ profile, defaults }: Props) {
  const keys = profile.keys;
  return (
    <Section
      title="Keys"
      description="Click a key, then press the key you use in WoW."
      onReset={() => patchProfile({ keys: defaults.keys })}
    >
      {profile.mode === "A" && (
        <Field
          label="Fishing key"
          help="The one-button addon's key: casts the line, and reels in when pressed again."
          defaultText={defaults.keys.fish_key}
          modified={differs(keys.fish_key, defaults.keys.fish_key)}
        >
          <KeyCapture
            label="Fishing key"
            value={keys.fish_key}
            onChange={(fish_key) => patchProfile({ keys: { fish_key } })}
          />
        </Field>
      )}
      {profile.mode !== "A" && (
        <Field
          label="Cast key"
          help="The action bar key with Fishing on it."
          defaultText={defaults.keys.cast_key}
          modified={differs(keys.cast_key, defaults.keys.cast_key)}
        >
          <KeyCapture
            label="Cast key"
            value={keys.cast_key}
            onChange={(cast_key) => patchProfile({ keys: { cast_key } })}
          />
        </Field>
      )}
      {profile.mode === "C" && (
        <Field
          label="Interact key"
          help="WoW's Interact With Target key, used to reel in the soft-targeted bobber."
          defaultText={defaults.keys.interact_key}
          modified={differs(keys.interact_key, defaults.keys.interact_key)}
        >
          <KeyCapture
            label="Interact key"
            value={keys.interact_key}
            onChange={(interact_key) => patchProfile({ keys: { interact_key } })}
          />
        </Field>
      )}
    </Section>
  );
}

function TimingSection({ profile, defaults }: Props) {
  const t = profile.timing;
  const d = defaults.timing;
  const row = (
    key: keyof Profile["timing"],
    label: string,
    help: string,
    min: number,
    max: number,
    step: number,
  ) => (
    <Field
      key={key}
      label={label}
      help={help}
      defaultText={`${d[key]} ms`}
      modified={t[key] !== d[key]}
    >
      <NumberInput
        value={t[key]}
        min={min}
        max={max}
        step={step}
        unit="ms"
        onCommit={(value) => value !== null && patchProfile({ timing: { [key]: value } })}
      />
    </Field>
  );
  return (
    <Section
      title="Timing"
      description="Measured in WoW Forever: the bobber settles within 1.3 s, fish bite 7 to 26 s after the cast."
      onReset={() => patchProfile({ timing: d })}
    >
      {row("land_delay_ms", "Listen after", "From the cast until the bot starts listening for a bite. Long enough for the bobber's landing splash to pass.", 0, 5000, 100)}
      {row("max_wait_ms", "Give up after", "From the cast until the bot counts a miss and casts again.", 1000, 60000, 1000)}
      {row("hook_delay_ms", "Reaction time", "From hearing the bite until reeling in. The game allows a few seconds.", 0, 3000, 50)}
      {row("post_hook_delay_ms", "Loot time", "After reeling in, before doing anything else. Auto Loot needs about 0.7 s.", 0, 5000, 100)}
      {row("between_casts_ms", "Pause between casts", "Extra wait before the next cast.", 0, 10000, 100)}
      {row("jitter_ms", "Random extra", "Up to this much is added to each wait, so the rhythm is not machine-perfect.", 0, 2000, 10)}
    </Section>
  );
}

function MouseSection({ profile, defaults }: Props) {
  const m = profile.mouse;
  const d = defaults.mouse;
  return (
    <Section
      title="Mouse"
      description="Only mode B uses the mouse."
      onReset={() => patchProfile({ mouse: d })}
    >
      <Field
        label="Shift + right-click"
        help="Loots the catch when Auto Loot is off."
        defaultText={d.shift_right_click ? "on" : "off"}
        modified={m.shift_right_click !== d.shift_right_click}
      >
        <Toggle
          label="Shift + right-click"
          checked={m.shift_right_click}
          onChange={(shift_right_click) => patchProfile({ mouse: { shift_right_click } })}
        />
      </Field>
      <Field
        label="Put the cursor back"
        help="After clicking the bobber, move the cursor back to where it was."
        defaultText={d.restore_cursor ? "on" : "off"}
        modified={m.restore_cursor !== d.restore_cursor}
      >
        <Toggle
          label="Put the cursor back"
          checked={m.restore_cursor}
          onChange={(restore_cursor) => patchProfile({ mouse: { restore_cursor } })}
        />
      </Field>
      <Field
        label="Movement steps"
        help="The cursor glides to the bobber in this many steps instead of jumping."
        defaultText={String(d.move_steps)}
        modified={m.move_steps !== d.move_steps}
      >
        <NumberInput
          value={m.move_steps}
          min={1}
          max={40}
          onCommit={(move_steps) => move_steps !== null && patchProfile({ mouse: { move_steps } })}
        />
      </Field>
      <Field
        label="Click length"
        help="How long the mouse button is held down."
        defaultText={`${d.click_hold_ms} ms`}
        modified={m.click_hold_ms !== d.click_hold_ms}
      >
        <NumberInput
          value={m.click_hold_ms}
          min={5}
          max={200}
          unit="ms"
          onCommit={(click_hold_ms) =>
            click_hold_ms !== null && patchProfile({ mouse: { click_hold_ms } })
          }
        />
      </Field>
    </Section>
  );
}
