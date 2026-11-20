import type { Patch, Profile, Settings } from "@/lib/config";
import { sendCommand } from "@/lib/ws";
import { useEngineStore } from "@/store/engineStore";

/** The active profile, typed; `null` until the engine has sent its snapshot. */
export function useProfile(): Profile | null {
  return useEngineStore((store) => store.profile as Profile | null);
}

export function useSettings(): Settings | null {
  return useEngineStore((store) => store.settings as Settings | null);
}

export function useDefaults(): { profile: Profile; settings: Settings } | null {
  return useEngineStore(
    (store) => store.defaults as { profile: Profile; settings: Settings } | null,
  );
}

/** Change part of the active profile. A running session applies it at the next cast. */
export function patchProfile(patch: Patch<Profile>): void {
  sendCommand({ type: "set_config", patch: patch as Record<string, unknown> });
}

/** Change machine-wide settings; the engine saves them straight away. */
export function patchSettings(patch: Patch<Settings>): void {
  sendCommand({ type: "set_settings", patch: patch as Record<string, unknown> });
}
