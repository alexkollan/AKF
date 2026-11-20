import { useEffect, useState, type ReactNode } from "react";
import { Info, RotateCcw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

/* ------------------------------------------------------------------------------------ */
/* layout                                                                                */
/* ------------------------------------------------------------------------------------ */

interface SectionProps {
  title: string;
  description?: string;
  onReset?: () => void;
  children: ReactNode;
}

/** A group of related fields. "Reset" puts only this group back to the factory values. */
export function Section({ title, description, onReset, children }: SectionProps) {
  return (
    <section className="border-t border-shoal/70 pt-5 first:border-t-0 first:pt-0">
      <header className="mb-3 flex items-baseline gap-3">
        <div className="min-w-0">
          <h2 className="text-[15px] font-semibold tracking-tight">{title}</h2>
          {description && <p className="mt-0.5 max-w-prose text-sm text-mist">{description}</p>}
        </div>
        {onReset && (
          <Button variant="ghost" size="sm" className="ml-auto text-mist" onClick={onReset}>
            <RotateCcw />
            Reset to defaults
          </Button>
        )}
      </header>
      <div className="flex flex-col">{children}</div>
    </section>
  );
}

interface FieldProps {
  label: string;
  help: string;
  /** Shown in the tooltip; when it differs from the current value the label gets a mark. */
  defaultText?: string;
  modified?: boolean;
  children: ReactNode;
  wide?: boolean;
}

/** One labelled control. The tooltip carries what it does and its default. */
export function Field({ label, help, defaultText, modified, children, wide }: FieldProps) {
  return (
    <div
      className={cn(
        "grid items-center gap-x-6 gap-y-2 py-2",
        wide ? "grid-cols-1" : "grid-cols-[minmax(12rem,16rem)_1fr]",
      )}
    >
      <div className="flex items-center gap-1.5 text-sm">
        <span>{label}</span>
        {modified && (
          <span
            className="size-1.5 rounded-full bg-lagoon"
            aria-label="changed from the default"
            title="Changed from the default"
          />
        )}
        <Tooltip>
          <TooltipTrigger
            className="rounded text-mist/70 hover:text-foam"
            aria-label={`About ${label}`}
          >
            <Info className="size-3.5" />
          </TooltipTrigger>
          <TooltipContent className="max-w-72 flex-col items-start text-left leading-snug">
            <span>{help}</span>
            {defaultText !== undefined && (
              <span className="opacity-70">Default: {defaultText}</span>
            )}
          </TooltipContent>
        </Tooltip>
      </div>
      <div className="min-w-0">{children}</div>
    </div>
  );
}

/* ------------------------------------------------------------------------------------ */
/* controls                                                                              */
/* ------------------------------------------------------------------------------------ */

interface NumberInputProps {
  value: number | null;
  onCommit: (value: number | null) => void;
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
  /** Allow an empty field, committed as null ("no limit"). */
  nullable?: boolean;
  placeholder?: string;
}

/** Edits locally; sends on Enter or when focus leaves, so typing "2500" is one change. */
export function NumberInput({
  value,
  onCommit,
  min,
  max,
  step = 1,
  unit,
  nullable,
  placeholder,
}: NumberInputProps) {
  const [draft, setDraft] = useState(value === null ? "" : String(value));
  useEffect(() => {
    setDraft(value === null ? "" : String(value));
  }, [value]);

  const commit = () => {
    if (draft.trim() === "") {
      if (nullable) {
        onCommit(null);
      } else {
        setDraft(value === null ? "" : String(value));
      }
      return;
    }
    const parsed = Number(draft);
    if (Number.isNaN(parsed)) {
      setDraft(value === null ? "" : String(value));
      return;
    }
    if (parsed !== value) {
      onCommit(parsed);
    }
  };

  return (
    <div className="flex items-center gap-2">
      <Input
        type="number"
        inputMode="decimal"
        className="w-28 tabular-nums"
        value={draft}
        min={min}
        max={max}
        step={step}
        placeholder={placeholder}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={commit}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            commit();
          }
        }}
      />
      {unit && <span className="text-sm text-mist">{unit}</span>}
    </div>
  );
}

interface SliderInputProps {
  value: number;
  onCommit: (value: number) => void;
  min: number;
  max: number;
  step: number;
  format: (value: number) => string;
}

/** Shows the value live while dragging; sends once, on release. */
export function SliderInput({ value, onCommit, min, max, step, format }: SliderInputProps) {
  const [live, setLive] = useState(value);
  useEffect(() => {
    setLive(value);
  }, [value]);
  return (
    <div className="flex items-center gap-4">
      <Slider
        className="max-w-80"
        value={live}
        min={min}
        max={max}
        step={step}
        onValueChange={(next) => setLive(Array.isArray(next) ? next[0] : next)}
        onValueCommitted={(next) => onCommit(Array.isArray(next) ? next[0] : next)}
      />
      <span className="w-24 text-sm tabular-nums text-mist">{format(live)}</span>
    </div>
  );
}

interface ToggleProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  label: string;
}

export function Toggle({ checked, onChange, disabled, label }: ToggleProps) {
  return (
    <Switch
      checked={checked}
      disabled={disabled}
      aria-label={label}
      onCheckedChange={(next) => onChange(Boolean(next))}
    />
  );
}

interface ChoiceProps<T extends string> {
  value: T;
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
  label: string;
}

export function Choice<T extends string>({ value, options, onChange, label }: ChoiceProps<T>) {
  return (
    <Select
      value={value}
      items={options}
      onValueChange={(next) => {
        if (next !== null) {
          onChange(next as T);
        }
      }}
    >
      <SelectTrigger className="min-w-56" aria-label={label}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {options.map((option) => (
          <SelectItem key={option.value} value={option.value}>
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

/** Two values equal for the "changed from default" mark (arrays and objects by content). */
export function differs(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) !== JSON.stringify(b);
}
