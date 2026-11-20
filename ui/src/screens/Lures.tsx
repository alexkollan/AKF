import { Plus, Sparkles, Trash2 } from "lucide-react";

import { KeyCapture } from "@/components/KeyCapture";
import { ScreenShell } from "@/components/ScreenShell";
import { Field, NumberInput, Section, Toggle } from "@/components/fields";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { Lure } from "@/lib/config";
import { patchProfile, useDefaults, useProfile } from "@/lib/engine";
import { sendCommand } from "@/lib/ws";
import { useEngineStore } from "@/store/engineStore";

const NEW_LURE: Lure = {
  name: "Shiny Bauble",
  key: "7",
  duration_min: 10,
  cast_time_s: 2,
  priority: 0,
  enabled: true,
};

export function Lures() {
  const profile = useProfile();
  const defaults = useDefaults()?.profile;
  const remaining = useEngineStore((store) => store.lureRemainingS);
  const running = useEngineStore((store) => store.status === "running");

  if (!profile || !defaults) {
    return <ScreenShell title="Lures" description="">{null}</ScreenShell>;
  }

  const lures = profile.lures.lures;
  const setLures = (next: Lure[]) => patchProfile({ lures: { lures: next } });
  const update = (index: number, change: Partial<Lure>) =>
    setLures(lures.map((lure, i) => (i === index ? { ...lure, ...change } : lure)));

  return (
    <ScreenShell
      title="Lures"
      description="The bot puts a lure on before a cast when the last one is about to run out — never in the middle of a cast."
      actions={
        <Button
          variant="outline"
          disabled={!running || lures.every((lure) => !lure.enabled)}
          onClick={() => sendCommand({ type: "apply_lure" })}
        >
          <Sparkles />
          Apply before the next cast
        </Button>
      }
    >
      <Section
        title="Automatic lures"
        onReset={() =>
          patchProfile({
            lures: {
              auto_apply: defaults.lures.auto_apply,
              refresh_margin_s: defaults.lures.refresh_margin_s,
            },
          })
        }
      >
        <Field
          label="Apply lures by themselves"
          help="Put the lure back on whenever it is about to expire."
          defaultText={defaults.lures.auto_apply ? "on" : "off"}
          modified={profile.lures.auto_apply !== defaults.lures.auto_apply}
        >
          <Toggle
            label="Apply lures by themselves"
            checked={profile.lures.auto_apply}
            onChange={(auto_apply) => patchProfile({ lures: { auto_apply } })}
          />
        </Field>
        <Field
          label="Renew this early"
          help="Re-apply when this little time is left, so the lure never drops off mid-cast."
          defaultText={`${defaults.lures.refresh_margin_s} s`}
          modified={profile.lures.refresh_margin_s !== defaults.lures.refresh_margin_s}
        >
          <NumberInput
            value={profile.lures.refresh_margin_s}
            min={0}
            max={300}
            unit="s before it runs out"
            onCommit={(refresh_margin_s) =>
              refresh_margin_s !== null && patchProfile({ lures: { refresh_margin_s } })
            }
          />
        </Field>
        <p className="py-2 text-sm text-mist">
          {remaining === null
            ? "No lure is on right now."
            : `The current lure has ${Math.floor(remaining / 60)} min ${Math.round(remaining % 60)} s left.`}
        </p>
      </Section>

      <Section
        title="Your lures"
        description="Only one lure is on the pole at a time: the enabled one with the highest priority. Bind its key to a macro such as /use Shiny Bauble then /use 16."
      >
        {lures.length === 0 ? (
          <div className="flex items-center gap-4 rounded-lg border border-dashed border-shoal p-5">
            <p className="text-sm text-mist">No lures yet. Add the one you use.</p>
            <Button variant="outline" className="ml-auto" onClick={() => setLures([NEW_LURE])}>
              <Plus />
              Add a lure
            </Button>
          </div>
        ) : (
          <>
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>Name</TableHead>
                  <TableHead>Key</TableHead>
                  <TableHead>Lasts</TableHead>
                  <TableHead>Cast time</TableHead>
                  <TableHead>Priority</TableHead>
                  <TableHead>On</TableHead>
                  <TableHead className="w-10" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {lures.map((lure, index) => (
                  <TableRow key={index}>
                    <TableCell>
                      <Input
                        // Remount when the row's lure changes, or a deleted name lingers.
                        key={`${index}-${lure.name}`}
                        aria-label="Lure name"
                        className="w-48"
                        defaultValue={lure.name}
                        onBlur={(event) => {
                          if (event.target.value.trim() && event.target.value !== lure.name) {
                            update(index, { name: event.target.value.trim() });
                          }
                        }}
                      />
                    </TableCell>
                    <TableCell>
                      <KeyCapture
                        label={`${lure.name} key`}
                        value={lure.key}
                        onChange={(key) => update(index, { key })}
                      />
                    </TableCell>
                    <TableCell>
                      <NumberInput
                        value={lure.duration_min}
                        min={0.5}
                        max={120}
                        step={0.5}
                        unit="min"
                        onCommit={(duration_min) =>
                          duration_min !== null && update(index, { duration_min })
                        }
                      />
                    </TableCell>
                    <TableCell>
                      <NumberInput
                        value={lure.cast_time_s}
                        min={0}
                        max={30}
                        step={0.5}
                        unit="s"
                        onCommit={(cast_time_s) =>
                          cast_time_s !== null && update(index, { cast_time_s })
                        }
                      />
                    </TableCell>
                    <TableCell>
                      <NumberInput
                        value={lure.priority}
                        min={0}
                        max={100}
                        onCommit={(priority) => priority !== null && update(index, { priority })}
                      />
                    </TableCell>
                    <TableCell>
                      <Switch
                        aria-label={`Use ${lure.name}`}
                        checked={lure.enabled}
                        onCheckedChange={(enabled) => update(index, { enabled: Boolean(enabled) })}
                      />
                    </TableCell>
                    <TableCell>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label={`Remove ${lure.name}`}
                        onClick={() => setLures(lures.filter((_, i) => i !== index))}
                      >
                        <Trash2 />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <div>
              <Button
                variant="outline"
                className="mt-3"
                onClick={() => setLures([...lures, { ...NEW_LURE, priority: lures.length }])}
              >
                <Plus />
                Add a lure
              </Button>
            </div>
          </>
        )}
      </Section>
    </ScreenShell>
  );
}
