import { useEffect, useState } from "react";

import { cn } from "@/lib/utils";

/**
 * Physical key (`KeyboardEvent.code`) to the key names `engine/input.py` understands.
 *
 * Mapping from the code rather than `event.key` keeps a Greek (or any) layout from turning
 * the B key into "β": the engine presses keys by position, so the UI records them by
 * position too.
 */
const NAMED: Record<string, string> = {
  BracketLeft: "[",
  BracketRight: "]",
  Semicolon: ";",
  Quote: "'",
  Comma: ",",
  Period: ".",
  Slash: "/",
  Backslash: "\\",
  Minus: "-",
  Equal: "=",
  Backquote: "`",
  Space: "space",
  Enter: "enter",
  Tab: "tab",
  Backspace: "backspace",
  Delete: "delete",
  Insert: "insert",
  Home: "home",
  End: "end",
  PageUp: "pageup",
  PageDown: "pagedown",
  ArrowUp: "up",
  ArrowDown: "down",
  ArrowLeft: "left",
  ArrowRight: "right",
  NumpadAdd: "numpadadd",
  NumpadSubtract: "numpadsubtract",
  NumpadMultiply: "numpadmultiply",
  NumpadDivide: "numpaddivide",
  NumpadDecimal: "numpaddecimal",
  NumpadEnter: "numpadenter",
};

export function keyNameFromCode(code: string): string | null {
  if (code in NAMED) {
    return NAMED[code];
  }
  let match = /^Key([A-Z])$/.exec(code);
  if (match) {
    return match[1].toLowerCase();
  }
  match = /^Digit(\d)$/.exec(code);
  if (match) {
    return match[1];
  }
  match = /^F(\d{1,2})$/.exec(code);
  if (match) {
    return `f${match[1]}`;
  }
  match = /^Numpad(\d)$/.exec(code);
  if (match) {
    return `numpad${match[1]}`;
  }
  return null;
}

/** How a key name is shown on a keycap: "]", "F9", "Num 5", "Space". */
export function keyLabel(name: string): string {
  const bare = name.replace(/^<|>$/g, "");
  if (/^f\d{1,2}$/.test(bare)) {
    return bare.toUpperCase();
  }
  if (bare.startsWith("numpad")) {
    return `Num ${bare.slice(6)}`;
  }
  if (bare.length === 1) {
    return bare.toUpperCase();
  }
  return bare.charAt(0).toUpperCase() + bare.slice(1);
}

interface KeyCaptureProps {
  value: string;
  onChange: (name: string) => void;
  /** Hotkeys are stored as "<f9>"; game keys as plain names. */
  asHotkey?: boolean;
  label: string;
}

/** A keycap. Click it, press the key you use in game, done. Escape cancels. */
export function KeyCapture({ value, onChange, asHotkey, label }: KeyCaptureProps) {
  const [listening, setListening] = useState(false);
  const [rejected, setRejected] = useState(false);

  useEffect(() => {
    if (!listening) {
      return;
    }
    const onKey = (event: KeyboardEvent) => {
      event.preventDefault(); // F5, F11, F12 and friends must not reach the window
      event.stopPropagation();
      if (event.code === "Escape") {
        setListening(false);
        return;
      }
      if (["ShiftLeft", "ShiftRight", "ControlLeft", "ControlRight", "AltLeft", "AltRight", "MetaLeft", "MetaRight"].includes(event.code)) {
        return; // wait for the real key
      }
      const name = keyNameFromCode(event.code);
      if (name === null) {
        setRejected(true);
        return;
      }
      setRejected(false);
      setListening(false);
      onChange(asHotkey ? `<${name}>` : name);
    };
    window.addEventListener("keydown", onKey, { capture: true });
    return () => window.removeEventListener("keydown", onKey, { capture: true });
  }, [listening, asHotkey, onChange]);

  return (
    <button
      type="button"
      aria-label={`${label}: ${keyLabel(value)}. Click to change.`}
      onClick={() => {
        setRejected(false);
        setListening((now) => !now);
      }}
      onBlur={() => setListening(false)}
      className={cn(
        "inline-flex h-9 min-w-12 items-center justify-center rounded-md border px-3 text-sm font-semibold transition-colors",
        "border-b-[3px] bg-depth-raised",
        listening ? "border-lagoon text-lagoon" : "border-shoal text-foam hover:border-mist",
      )}
    >
      {listening ? (rejected ? "That key cannot be bound" : "Press a key…") : keyLabel(value)}
    </button>
  );
}
