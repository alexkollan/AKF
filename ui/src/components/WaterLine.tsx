import { useMemo } from "react";
import {
  Area,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  XAxis,
  YAxis,
} from "recharts";

import type { CatchRecord, DetectorLevels } from "@/lib/protocol";

const WINDOW_MS = 60_000;
const Y_MIN = -80;
const Y_MAX = -10;

interface Point {
  t: number;
  /** Level while the bot listens (bright water) or not (still water); one is always null. */
  listening: number | null;
  idle: number | null;
  threshold: number;
  floor: number;
}

interface WaterLineProps {
  levels: DetectorLevels[];
  catches: CatchRecord[];
  height: number;
}

/**
 * The last minute of what the bite detector hears, drawn as a water line.
 *
 * The water is the in-band level; it lights up while the bot is listening for a bite and
 * stays dark while it casts, waits for the bobber or loots. The straight bright line is the
 * trigger threshold — the fishing line a splash has to break — and the faint line under it
 * is the noise floor the threshold rides on. Each hook leaves a mark where it happened.
 */
export function WaterLine({ levels, catches, height }: WaterLineProps) {
  const now = levels.length ? levels[levels.length - 1].t : Date.now();
  const start = now - WINDOW_MS;

  const data = useMemo<Point[]>(() => {
    const points: Point[] = [];
    let previousArmed: boolean | null = null;
    for (const level of levels) {
      if (level.t < start) {
        continue;
      }
      const value = clamp(level.audioLevel);
      // Repeat the boundary sample in both series so the two kinds of water meet.
      if (previousArmed !== null && previousArmed !== level.armed && points.length) {
        const last = points[points.length - 1];
        points.push({ ...last, listening: last.listening ?? last.idle, idle: last.idle ?? last.listening });
      }
      points.push({
        t: level.t,
        listening: level.armed ? value : null,
        idle: level.armed ? null : value,
        threshold: clamp(level.audioThreshold),
        floor: clamp(level.audioFloor),
      });
      previousArmed = level.armed;
    }
    return points;
  }, [levels, start]);

  const marks = catches.filter((record) => record.t >= start);

  if (data.length < 2) {
    return (
      <div
        className="flex items-center justify-center rounded-lg border border-dashed border-shoal text-sm text-mist"
        style={{ height }}
      >
        The water line appears once a session is running.
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={data} margin={{ top: 12, right: 8, bottom: 0, left: 0 }}>
        <defs>
          <linearGradient id="water-listening" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--chart-splash)" stopOpacity={0.55} />
            <stop offset="100%" stopColor="var(--chart-water)" stopOpacity={0.05} />
          </linearGradient>
          <linearGradient id="water-idle" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--chart-water)" stopOpacity={0.35} />
            <stop offset="100%" stopColor="var(--chart-water)" stopOpacity={0.02} />
          </linearGradient>
        </defs>
        <XAxis
          dataKey="t"
          type="number"
          domain={[start, now]}
          tickFormatter={(t: number) => `${Math.round((t - now) / 1000)}s`}
          ticks={[start, start + 15_000, start + 30_000, start + 45_000, now]}
          stroke="var(--shoal)"
          tick={{ fill: "var(--mist)", fontSize: 11 }}
          tickLine={false}
        />
        <YAxis
          domain={[Y_MIN, Y_MAX]}
          ticks={[-70, -50, -30]}
          tickFormatter={(db: number) => `${db} dB`}
          stroke="var(--shoal)"
          tick={{ fill: "var(--mist)", fontSize: 11 }}
          tickLine={false}
          axisLine={false}
          width={52}
        />
        <Area
          dataKey="idle"
          type="monotone"
          stroke="var(--chart-water)"
          strokeWidth={1}
          fill="url(#water-idle)"
          baseValue={Y_MIN}
          isAnimationActive={false}
          connectNulls={false}
        />
        <Area
          dataKey="listening"
          type="monotone"
          stroke="var(--chart-splash)"
          strokeWidth={1.5}
          fill="url(#water-listening)"
          baseValue={Y_MIN}
          isAnimationActive={false}
          connectNulls={false}
        />
        <Line
          dataKey="floor"
          type="monotone"
          stroke="var(--mist)"
          strokeOpacity={0.35}
          strokeDasharray="2 4"
          dot={false}
          isAnimationActive={false}
        />
        <Line
          dataKey="threshold"
          type="stepAfter"
          stroke="var(--chart-line)"
          strokeWidth={1.25}
          dot={false}
          isAnimationActive={false}
        />
        {marks.map((record) => (
          <ReferenceLine
            key={record.t}
            x={record.t}
            stroke="var(--state-running)"
            strokeOpacity={0.8}
            label={{ value: "hooked", position: "insideTopLeft", fill: "var(--state-running)", fontSize: 11 }}
          />
        ))}
      </ComposedChart>
    </ResponsiveContainer>
  );
}

function clamp(db: number): number {
  return Math.min(Y_MAX, Math.max(Y_MIN, db));
}
