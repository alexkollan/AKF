import { KeyCapture } from "@/components/KeyCapture";
import { ScreenShell } from "@/components/ScreenShell";
import { Choice, Field, NumberInput, Section, Toggle } from "@/components/fields";
import { Input } from "@/components/ui/input";
import type { Profile, Settings as SettingsModel } from "@/lib/config";
import { patchProfile, patchSettings, useDefaults, useProfile, useSettings } from "@/lib/engine";

export function Settings() {
  const profile = useProfile();
  const settings = useSettings();
  const defaults = useDefaults();

  return (
    <ScreenShell
      title="Settings"
      description="Keys that work from inside the game, when the bot stops by itself, and how this window behaves."
    >
      {profile && settings && defaults && (
        <>
          <HotkeysSection settings={settings} defaults={defaults.settings} />
          <StopSection profile={profile} defaults={defaults.profile} />
          <NotificationsSection settings={settings} defaults={defaults.settings} />
          <WindowSection settings={settings} defaults={defaults.settings} />
        </>
      )}
    </ScreenShell>
  );
}

interface SettingsProps {
  settings: SettingsModel;
  defaults: SettingsModel;
}

function HotkeysSection({ settings, defaults }: SettingsProps) {
  const keys = settings.hotkeys;
  const d = defaults.hotkeys;
  const row = (key: keyof SettingsModel["hotkeys"], label: string, help: string) => (
    <Field key={key} label={label} help={help} defaultText={d[key]} modified={keys[key] !== d[key]}>
      <KeyCapture
        asHotkey
        label={label}
        value={keys[key]}
        onChange={(value) => patchSettings({ hotkeys: { [key]: value } })}
      />
    </Field>
  );
  return (
    <Section
      title="Hotkeys"
      description="These work while WoW is in front, and WoW never sees them, so they can share keys with your bags."
      onReset={() => patchSettings({ hotkeys: d })}
    >
      {row("start_stop", "Start or stop", "Starts fishing when stopped, stops when running or paused.")}
      {row("pause", "Pause or resume", "Pauses the bot without ending the session.")}
      {row("emergency_stop", "Emergency stop", "Lets go of every key and stops at once.")}
    </Section>
  );
}

function StopSection({ profile, defaults }: { profile: Profile; defaults: Profile }) {
  const stop = profile.stop;
  const d = defaults.stop;
  return (
    <Section
      title="When to stop by itself"
      description="Saved with the profile. Leave a limit empty for no limit."
      onReset={() => patchProfile({ stop: d })}
    >
      <Field
        label="Stop after"
        help="End the session after this many minutes of fishing. Paused time does not count."
        defaultText="no limit"
        modified={stop.max_duration_min !== d.max_duration_min}
      >
        <NumberInput
          nullable
          value={stop.max_duration_min}
          min={1}
          placeholder="no limit"
          unit="minutes"
          onCommit={(max_duration_min) => patchProfile({ stop: { max_duration_min } })}
        />
      </Field>
      <Field
        label="Stop at"
        help="End the session once this many fish have been hooked."
        defaultText="no limit"
        modified={stop.max_catches !== d.max_catches}
      >
        <NumberInput
          nullable
          value={stop.max_catches}
          min={1}
          placeholder="no limit"
          unit="fish"
          onCommit={(max_catches) => patchProfile({ stop: { max_catches } })}
        />
      </Field>
      <Field
        label="Pause after misses"
        help="Pause when this many casts in a row end without a bite. Usually means the sound settings or the spot need a look."
        defaultText={`${d.consecutive_misses} in a row`}
        modified={stop.consecutive_misses !== d.consecutive_misses}
      >
        <NumberInput
          value={stop.consecutive_misses}
          min={1}
          max={100}
          unit="in a row"
          onCommit={(consecutive_misses) =>
            consecutive_misses !== null && patchProfile({ stop: { consecutive_misses } })
          }
        />
      </Field>
      <Field
        label="Pause when WoW loses focus"
        help="Keys always go to the window in front. Pausing the moment WoW is not in front keeps them out of other apps."
        defaultText="on"
        modified={stop.pause_on_focus_loss !== d.pause_on_focus_loss}
      >
        <Toggle
          label="Pause when WoW loses focus"
          checked={stop.pause_on_focus_loss}
          onChange={(pause_on_focus_loss) => patchProfile({ stop: { pause_on_focus_loss } })}
        />
      </Field>
      <Field
        label="Stop when bags are full"
        help="Needs the FishBridge addon, which comes with the next milestone."
        defaultText="on"
        modified={stop.stop_on_bags_full !== d.stop_on_bags_full}
      >
        <Toggle
          label="Stop when bags are full"
          checked={stop.stop_on_bags_full}
          onChange={(stop_on_bags_full) => patchProfile({ stop: { stop_on_bags_full } })}
        />
      </Field>
      <Field
        label="Pause when attacked"
        help="Needs the FishBridge addon, which comes with the next milestone."
        defaultText="on"
        modified={stop.pause_on_combat !== d.pause_on_combat}
      >
        <Toggle
          label="Pause when attacked"
          checked={stop.pause_on_combat}
          onChange={(pause_on_combat) => patchProfile({ stop: { pause_on_combat } })}
        />
      </Field>
    </Section>
  );
}

function NotificationsSection({ settings, defaults }: SettingsProps) {
  const n = settings.notifications;
  return (
    <Section
      title="Tell me when it stops"
      description="Sent when the bot stops or pauses by itself, never when you stop it."
      onReset={() => patchSettings({ notifications: defaults.notifications })}
    >
      <Field
        label="Webhook"
        help="A Discord or n8n webhook address. The message says why the bot stopped and how many fish it hooked."
        defaultText="none"
        modified={n.webhook_url !== defaults.notifications.webhook_url}
      >
        <Input
          key={n.webhook_url ?? ""}
          className="max-w-md"
          type="url"
          defaultValue={n.webhook_url ?? ""}
          placeholder="https://discord.com/api/webhooks/…"
          onBlur={(event) => {
            const value = event.target.value.trim() || null;
            if (value !== n.webhook_url) {
              patchSettings({ notifications: { webhook_url: value } });
            }
          }}
        />
      </Field>
      <Field
        label="Windows notification"
        help="Not available yet; use the webhook in the meantime."
        defaultText="on"
      >
        <Toggle label="Windows notification" checked={false} disabled onChange={() => undefined} />
      </Field>
    </Section>
  );
}

function WindowSection({ settings, defaults }: SettingsProps) {
  const w = settings.window;
  const d = defaults.window;
  return (
    <Section
      title="This window"
      description="Takes effect the next time the window opens, except Always on top."
      onReset={() =>
        patchSettings({
          window: { monitor_index: d.monitor_index, always_on_top: d.always_on_top },
          dry_run: defaults.dry_run,
        })
      }
    >
      <Field
        label="Open on"
        help="The screen the window opens on. It also remembers where you last left it."
        defaultText="second screen"
        modified={w.monitor_index !== d.monitor_index}
      >
        <Choice
          label="Open on"
          value={String(w.monitor_index)}
          options={[
            { value: "0", label: "Main screen" },
            { value: "1", label: "Second screen" },
            { value: "2", label: "Third screen" },
          ]}
          onChange={(value) => patchSettings({ window: { monitor_index: Number(value) } })}
        />
      </Field>
      <Field
        label="Always on top"
        help="Keep this window above others. It still never takes focus away from WoW on its own."
        defaultText="off"
        modified={w.always_on_top !== d.always_on_top}
      >
        <Toggle
          label="Always on top"
          checked={w.always_on_top}
          onChange={(always_on_top) => patchSettings({ window: { always_on_top } })}
        />
      </Field>
      <Field
        label="Practice mode"
        help="Listen and decide as usual, but press no keys: every press is written to the log instead."
        defaultText="off"
        modified={settings.dry_run !== defaults.dry_run}
      >
        <Toggle
          label="Practice mode"
          checked={settings.dry_run}
          onChange={(dry_run) => patchSettings({ dry_run })}
        />
      </Field>
    </Section>
  );
}
