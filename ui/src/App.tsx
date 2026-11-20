import { useEffect, useState } from "react";
import {
  Crosshair,
  Fish,
  Gauge,
  Keyboard,
  Settings as SettingsIcon,
  SlidersHorizontal,
  Wand2,
} from "lucide-react";

import { connectEngine } from "@/lib/ws";
import { cn } from "@/lib/utils";
import { useEngineStore } from "@/store/engineStore";
import { Calibration } from "@/screens/Calibration";
import { Dashboard } from "@/screens/Dashboard";
import { Detection } from "@/screens/Detection";
import { Lures } from "@/screens/Lures";
import { ModeKeys } from "@/screens/ModeKeys";
import { Profiles } from "@/screens/Profiles";
import { Settings } from "@/screens/Settings";

/**
 * Screen registry. The grind bot adds its own entries here later, which is why the
 * sidebar is driven by data instead of hard-coded markup.
 */
const SCREENS = [
  { id: "dashboard", label: "Dashboard", icon: Gauge, Component: Dashboard },
  { id: "mode", label: "Mode & Keys", icon: Keyboard, Component: ModeKeys },
  { id: "lures", label: "Lures", icon: Fish, Component: Lures },
  { id: "detection", label: "Detection", icon: Crosshair, Component: Detection },
  { id: "calibration", label: "Calibration", icon: Wand2, Component: Calibration },
  { id: "profiles", label: "Profiles", icon: SlidersHorizontal, Component: Profiles },
  { id: "settings", label: "Settings", icon: SettingsIcon, Component: Settings },
] as const;

type ScreenId = (typeof SCREENS)[number]["id"];

export default function App() {
  const [active, setActive] = useState<ScreenId>("dashboard");
  const connected = useEngineStore((store) => store.connected);

  useEffect(() => connectEngine(), []);

  const current = SCREENS.find((screen) => screen.id === active) ?? SCREENS[0];
  const Current = current.Component;

  return (
    <div className="flex h-full">
      <nav className="flex w-52 shrink-0 flex-col gap-1 border-r border-border bg-card/50 p-3">
        <div className="mb-3 flex items-center gap-2 px-2">
          <Fish className="size-5 text-primary" />
          <span className="font-semibold tracking-tight">Fishbot</span>
        </div>
        {SCREENS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            type="button"
            onClick={() => setActive(id)}
            className={cn(
              "flex items-center gap-2 rounded-md px-2 py-2 text-left text-sm transition-colors",
              id === active
                ? "bg-primary/15 text-foreground"
                : "text-muted-foreground hover:bg-accent hover:text-foreground",
            )}
          >
            <Icon className="size-4" />
            {label}
          </button>
        ))}
        <div className="mt-auto flex items-center gap-2 px-2 py-1 text-xs text-muted-foreground">
          <span
            className={cn(
              "size-2 rounded-full",
              connected ? "bg-state-running" : "bg-state-error",
            )}
          />
          {connected ? "engine online" : "engine offline"}
        </div>
      </nav>
      <main className="min-w-0 flex-1">
        <Current />
      </main>
    </div>
  );
}
