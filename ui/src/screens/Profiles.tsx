import { useRef, useState } from "react";
import { Copy, Download, FolderOpen, Save, Trash2, Upload } from "lucide-react";

import { ScreenShell } from "@/components/ScreenShell";
import { Field, Section } from "@/components/fields";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { patchProfile, useProfile } from "@/lib/engine";
import { cn } from "@/lib/utils";
import { sendCommand } from "@/lib/ws";
import { useEngineStore } from "@/store/engineStore";

export function Profiles() {
  const profile = useProfile();
  const { names, active } = useEngineStore((store) => store.profiles);
  const fileInput = useRef<HTMLInputElement>(null);
  const [saveAs, setSaveAs] = useState("");
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  if (!profile) {
    return <ScreenShell title="Profiles" description="">{null}</ScreenShell>;
  }

  const exportProfile = () => {
    const blob = new Blob([JSON.stringify(profile, null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `${profile.name}.json`;
    link.click();
    URL.revokeObjectURL(link.href);
  };

  const importProfile = async (file: File) => {
    try {
      const parsed = JSON.parse(await file.text()) as Record<string, unknown>;
      sendCommand({ type: "import_profile", profile: parsed });
    } catch {
      useEngineStore.getState().applyEvent({
        type: "error",
        message: `${file.name} is not a JSON file.`,
        command: "import_profile",
      });
    }
  };

  const saveNamed = () => {
    const name = saveAs.trim();
    if (name) {
      sendCommand({ type: "save_profile", name });
      setSaveAs("");
    }
  };

  return (
    <ScreenShell
      title="Profiles"
      description="One profile per fishing spot: mode, keys, lures, detection, search area and stop rules travel together."
      actions={
        <>
          <Button variant="outline" onClick={() => fileInput.current?.click()}>
            <Upload />
            Import
          </Button>
          <Button variant="outline" onClick={exportProfile}>
            <Download />
            Export
          </Button>
          <input
            ref={fileInput}
            type="file"
            accept="application/json,.json"
            className="hidden"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) {
                void importProfile(file);
              }
              event.target.value = "";
            }}
          />
        </>
      }
    >
      <Section title="This profile" description="Changes apply at once and are kept until you switch profile; save to keep them for next time.">
        <Field label="Name" help="Saving under a new name makes a copy and switches to it.">
          <p className="text-sm font-semibold">{profile.name}</p>
        </Field>
        <Field label="Notes" help="Anything worth remembering about this spot: time of day, weather, quirks.">
          <Input
            key={profile.notes}
            className="max-w-md"
            defaultValue={profile.notes}
            placeholder="e.g. Stranglethorn coast, night, low waves"
            onBlur={(event) => {
              if (event.target.value !== profile.notes) {
                patchProfile({ notes: event.target.value });
              }
            }}
          />
        </Field>
        <div className="flex flex-wrap items-center gap-2 py-2">
          <Button onClick={() => sendCommand({ type: "save_profile" })}>
            <Save />
            Save {profile.name}
          </Button>
          <span className="mx-2 text-sm text-mist">or save as</span>
          <Input
            aria-label="New profile name"
            className="w-56"
            value={saveAs}
            placeholder="STV coast night"
            onChange={(event) => setSaveAs(event.target.value)}
            onKeyDown={(event) => event.key === "Enter" && saveNamed()}
          />
          <Button variant="outline" disabled={!saveAs.trim()} onClick={saveNamed}>
            <Copy />
            Save a copy
          </Button>
        </div>
      </Section>

      <Section title="Saved profiles">
        {names.length === 0 ? (
          <p className="rounded-lg border border-dashed border-shoal p-5 text-sm text-mist">
            Nothing saved yet. Save this profile to keep it, then make one for each spot you fish.
          </p>
        ) : (
          <ul className="flex flex-col divide-y divide-shoal/70 rounded-lg border border-shoal">
            {names.map((name) => (
              <li key={name} className="flex items-center gap-3 px-4 py-2.5">
                <span className={cn("text-sm", name === active && "font-semibold")}>{name}</span>
                {name === active && (
                  <span className="rounded bg-lagoon/15 px-1.5 py-0.5 text-xs text-lagoon">in use</span>
                )}
                <span className="ml-auto flex gap-1.5">
                  {name !== active && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => sendCommand({ type: "load_profile", name })}
                    >
                      <FolderOpen />
                      Use
                    </Button>
                  )}
                  {confirmDelete === name ? (
                    <>
                      <Button
                        variant="destructive"
                        size="sm"
                        onClick={() => {
                          sendCommand({ type: "delete_profile", name });
                          setConfirmDelete(null);
                        }}
                      >
                        Delete {name}
                      </Button>
                      <Button variant="ghost" size="sm" onClick={() => setConfirmDelete(null)}>
                        Keep
                      </Button>
                    </>
                  ) : (
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      aria-label={`Delete ${name}`}
                      onClick={() => setConfirmDelete(name)}
                    >
                      <Trash2 />
                    </Button>
                  )}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Section>
    </ScreenShell>
  );
}
