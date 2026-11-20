import { useRef, useState, type PointerEvent } from "react";

import type { HsvRange, RoiFraction } from "@/lib/config";

interface RoiEditorProps {
  pngBase64: string;
  roi: RoiFraction;
  onRoi: (roi: RoiFraction) => void;
  /** When set, a click samples the colour under the cursor instead of drawing. */
  onPick?: (range: HsvRange) => void;
}

const MIN_SIDE = 0.03;

/**
 * A snapshot of the WoW window with the search area drawn on it. Drag to draw a new
 * area. In eyedropper mode a click samples the bobber's colour instead.
 */
export function RoiEditor({ pngBase64, roi, onRoi, onPick }: RoiEditorProps) {
  const frame = useRef<HTMLDivElement>(null);
  const image = useRef<HTMLImageElement>(null);
  const [draft, setDraft] = useState<RoiFraction | null>(null);
  const [anchor, setAnchor] = useState<{ x: number; y: number } | null>(null);

  const fraction = (event: PointerEvent) => {
    const box = frame.current!.getBoundingClientRect();
    return {
      x: Math.min(1, Math.max(0, (event.clientX - box.left) / box.width)),
      y: Math.min(1, Math.max(0, (event.clientY - box.top) / box.height)),
    };
  };

  const shown = draft ?? roi;

  return (
    <div
      ref={frame}
      className={`relative select-none overflow-hidden rounded-md border border-shoal ${onPick ? "cursor-crosshair" : "cursor-cell"}`}
      onPointerDown={(event) => {
        const point = fraction(event);
        if (onPick) {
          const range = sample(image.current!, point.x, point.y);
          if (range) {
            onPick(range);
          }
          return;
        }
        event.currentTarget.setPointerCapture(event.pointerId);
        setAnchor(point);
        setDraft({ x: point.x, y: point.y, w: 0, h: 0 });
      }}
      onPointerMove={(event) => {
        if (!anchor) {
          return;
        }
        const point = fraction(event);
        setDraft({
          x: Math.min(anchor.x, point.x),
          y: Math.min(anchor.y, point.y),
          w: Math.abs(point.x - anchor.x),
          h: Math.abs(point.y - anchor.y),
        });
      }}
      onPointerUp={() => {
        if (draft && draft.w >= MIN_SIDE && draft.h >= MIN_SIDE) {
          onRoi(round(draft));
        }
        setAnchor(null);
        setDraft(null);
      }}
    >
      <img
        ref={image}
        alt="The WoW window"
        draggable={false}
        src={`data:image/png;base64,${pngBase64}`}
        className="block w-full"
      />
      {/* Everything outside the search area is dimmed; the area itself stays clear. */}
      <div
        className="pointer-events-none absolute border-2 border-lagoon shadow-[0_0_0_9999px_rgba(14,26,33,0.55)]"
        style={{
          left: `${shown.x * 100}%`,
          top: `${shown.y * 100}%`,
          width: `${shown.w * 100}%`,
          height: `${shown.h * 100}%`,
        }}
      />
    </div>
  );
}

function round(roi: RoiFraction): RoiFraction {
  const r = (value: number) => Math.round(value * 1000) / 1000;
  return { x: r(roi.x), y: r(roi.y), w: r(roi.w), h: r(roi.h) };
}

/**
 * The colour under the click as an OpenCV-style HSV range (H 0-179, S and V 0-255),
 * widened enough to keep matching the feather as the light on the water changes.
 */
function sample(img: HTMLImageElement, fx: number, fy: number): HsvRange | null {
  const canvas = document.createElement("canvas");
  canvas.width = img.naturalWidth;
  canvas.height = img.naturalHeight;
  const context = canvas.getContext("2d");
  if (!context) {
    return null;
  }
  context.drawImage(img, 0, 0);
  const px = Math.round(fx * (img.naturalWidth - 1));
  const py = Math.round(fy * (img.naturalHeight - 1));
  // Average a 3x3 patch: one pixel of a small bobber is mostly noise.
  const patch = context.getImageData(Math.max(0, px - 1), Math.max(0, py - 1), 3, 3).data;
  let red = 0;
  let green = 0;
  let blue = 0;
  const count = patch.length / 4;
  for (let i = 0; i < patch.length; i += 4) {
    red += patch[i];
    green += patch[i + 1];
    blue += patch[i + 2];
  }
  const [h, s, v] = rgbToHsv(red / count, green / count, blue / count);
  return {
    h_min: Math.max(0, h - 8),
    h_max: Math.min(179, h + 8),
    s_min: Math.max(40, s - 70),
    s_max: 255,
    v_min: Math.max(40, v - 70),
    v_max: 255,
  };
}

function rgbToHsv(r: number, g: number, b: number): [number, number, number] {
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const delta = max - min;
  let hue = 0;
  if (delta > 0) {
    if (max === r) {
      hue = ((g - b) / delta) % 6;
    } else if (max === g) {
      hue = (b - r) / delta + 2;
    } else {
      hue = (r - g) / delta + 4;
    }
    hue *= 60;
    if (hue < 0) {
      hue += 360;
    }
  }
  const saturation = max === 0 ? 0 : delta / max;
  return [Math.round(hue / 2), Math.round(saturation * 255), Math.round(max)];
}
