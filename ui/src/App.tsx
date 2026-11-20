import { useEffect, useState, type ComponentType } from "react";
import {
  Crosshair,
  Fish,
  Gauge,
  Keyboard,
  Settings as SettingsIcon,
  SlidersHorizontal,
  Wand2,
  X,
} from "lucide-react";

import { TooltipProvider } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { connectEngine } from "@/lib/ws";
import { Calibration } from "@/screens/Calibration";
import { Dashboard } from "@/screens/Dashboard";
import { Detection } from "@/screens/Detection";
import { Lures } from "@/screens/Lures";
import { ModeKeys } from "@/screens/ModeKeys";
import { Profiles } from "@/screens/Profiles";
import { Settings } from "@/screens/Settings";
import { useEngineStore } from "@/store/engineStore";

interface ScreenProps {
  openScreen: (id: string) => void;
}

/**
 * Screen registry. The grind bot appends its own entries here later, which is why the
 * sidebar is driven by data instead of hard-coded markup.
 */
const SCREENS: { id: string; label: string; icon: ComponentType<{ className?: string }>; Component: ComponentType<ScreenProps> }[] = [
  { id: "dashboard", label: "Dashboard", icon: Gauge, Component: Dashboard },
  { id: "mode", label: "Mode & keys", icon: Keyboard, Component: ModeKeys },
  { id: "lures", label: "Lures", icon: Fish, Component: Lures },
  { id: "detection", label: "Detection", icon: Crosshair, Component: Detection },
  { id: "calibration", label: "Calibration", icon: Wand2, Component: Calibration },
  { id: "profiles", label: "Profiles", icon: SlidersHorizontal, Component: Profiles },
  { id: "settings", label: "Settings", icon: SettingsIcon, Component: Settings },
];

export default function App() {
  const [active, setActive] = useState("dashboard");
  const connected = useEngineStore((store) => store.connected);
  const status = useEngineStore((store) => store.status);
  const profileName = useEngineStore((store) => store.profiles.active);

  useEffect(() => connectEngine(), []);

  const current = SCREENS.find((screen) => screen.id === active) ?? SCREENS[0];
  const Current = current.Component;

  return (
    <TooltipProvider delay={300}>
      <div className="flex h-full">
        <nav className="flex w-52 shrink-0 flex-col gap-0.5 border-r border-shoal bg-depth/60 p-3">
          <div className="mb-5 px-2 pt-1">
            <p className="text-lg font-semibold tracking-tight [font-stretch:112%]">Fishbot</p>
            <p className="truncate text-xs text-mist" title="Active profile">
              {profileName || "no profile"}
            </p>
          </div>
          {SCREENS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              type="button"
              onClick={() => setActive(id)}
              aria-current={id === active ? "page" : undefined}
              className={cn(
                "flex items-center gap-2.5 rounded-md px-2 py-2 text-left text-sm transition-colors",
                id === active
                  ? "bg-lagoon/15 text-foam"
                  : "text-mist hover:bg-depth-raised hover:text-foam",
              )}
            >
              <Icon className={cn("size-4", id === active && "text-lagoon")} />
              {label}
              {id === "dashboard" && status !== "stopped" && (
                <span
                  className={cn(
                    "ml-auto size-2 rounded-full",
                    status === "running" && "bg-state-running",
                    status === "paused" && "bg-state-paused",
                    status === "error" && "bg-state-error",
                  )}
                  aria-label={status}
                />
              )}
            </button>
          ))}
          <div className="mt-auto flex items-center gap-2 px-2 py-1 text-xs text-mist">
            <span
              className={cn("size-2 rounded-full", connected ? "bg-state-running" : "bg-state-error")}
            />
            {connected ? "Engine connected" : "Engine not running"}
          </div>
        </nav>
        <main className="min-w-0 flex-1 overflow-hidden">
          <Current openScreen={setActive} />
        </main>
        <ErrorToast />
      </div>
    </TooltipProvider>
  );
}

/** The engine refused something: say what, until dismissed or replaced. */
function ErrorToast() {
  const error = useEngineStore((store) => store.lastError);
  const [hidden, setHidden] = useState<number | null>(null);

  useEffect(() => {
    if (!error) {
      return;
    }
    const timer = window.setTimeout(() => setHidden(error.at), 8000);
    return () => window.clearTimeout(timer);
  }, [error]);

  if (!error || hidden === error.at) {
    return null;
  }
  return (
    <div
      role="alert"
      className="fixed right-5 bottom-5 z-50 flex max-w-md items-start gap-3 rounded-lg border border-state-error/60 bg-depth-raised px-4 py-3 text-sm shadow-lg"
    >
      <div className="min-w-0">
        <p className="font-semibold text-state-error">
          {error.command ? `Could not ${error.command.replaceAll("_", " ")}` : "Engine error"}
        </p>
        <p className="mt-0.5 break-words text-foam">{error.message}</p>
      </div>
      <button
        type="button"
        aria-label="Dismiss"
        className="text-mist hover:text-foam"
        onClick={() => setHidden(error.at)}
      >
        <X className="size-4" />
      </button>
    </div>
  );
}
