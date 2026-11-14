"use client";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { SERIES, Tip } from "./EquityChart";

const INK = "#8591a8";

function RelTip({ active, payload }: any) {
  if (!active || !payload?.length) return null;
  const b = payload[0].payload;
  return (
    <div className="rounded-lg border border-surface-line bg-surface-sunk px-3 py-2 text-xs shadow-lg">
      <div className="mb-1 font-semibold text-ink">Confidence bin {b.bin}</div>
      <div className="text-ink-soft">mean confidence <b className="tabular-nums text-ink">{(b.confidence * 100).toFixed(1)}%</b></div>
      <div className="text-ink-soft">accuracy <b className="tabular-nums text-ink">{(b.accuracy * 100).toFixed(1)}%</b></div>
      <div className="text-ink-soft">{b.n} decisions</div>
    </div>
  );
}

/** Reliability diagram: accuracy against stated confidence. On the dashed diagonal = perfectly calibrated. */
export function ReliabilityChart({ bins }: { bins: any[] }) {
  const data = bins.filter((b) => b.n > 0);
  return (
    <ResponsiveContainer width="100%" height={300}>
      <LineChart data={data} margin={{ top: 8, right: 12, left: -8, bottom: 16 }}>
        <CartesianGrid stroke="#26334f" strokeDasharray="2 4" />
        <XAxis dataKey="confidence" type="number" domain={[0, 1]} ticks={[0, 0.2, 0.4, 0.6, 0.8, 1]} tick={{ fill: INK, fontSize: 12 }} axisLine={{ stroke: "#26334f" }} tickLine={false}
          label={{ value: "stated confidence", position: "insideBottom", offset: -8, fill: INK, fontSize: 12 }} />
        <YAxis type="number" domain={[0, 1]} ticks={[0, 0.2, 0.4, 0.6, 0.8, 1]} tick={{ fill: INK, fontSize: 12 }} axisLine={false} tickLine={false}
          label={{ value: "accuracy", angle: -90, position: "insideLeft", offset: 18, fill: INK, fontSize: 12 }} />
        <ReferenceLine segment={[{ x: 0, y: 0 }, { x: 1, y: 1 }]} stroke={INK} strokeDasharray="5 5" ifOverflow="visible" />
        <Tooltip content={<RelTip />} cursor={{ stroke: INK, strokeDasharray: "3 3" }} />
        <Line dataKey="accuracy" name="Jev" stroke={SERIES.registry} strokeWidth={2} dot={{ r: 5, fill: SERIES.registry, stroke: "#16213a", strokeWidth: 2 }} activeDot={{ r: 7 }} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function LatencyChart({ hist }: { hist: { bucket: string; n: number }[] }) {
  return (
    <ResponsiveContainer width="100%" height={300}>
      <BarChart data={hist} margin={{ top: 8, right: 8, left: -8, bottom: 0 }} barCategoryGap="18%">
        <CartesianGrid stroke="#26334f" strokeDasharray="2 4" vertical={false} />
        <XAxis dataKey="bucket" tick={{ fill: INK, fontSize: 10 }} axisLine={{ stroke: "#26334f" }} tickLine={false} interval={0} angle={-25} textAnchor="end" height={50} />
        <YAxis tick={{ fill: INK, fontSize: 12 }} axisLine={false} tickLine={false} allowDecimals={false} />
        <Tooltip content={<Tip unit=" requests" />} cursor={{ fill: "#26334f", opacity: 0.35 }} />
        <Bar dataKey="n" name="Requests" fill={SERIES.registry} radius={[4, 4, 0, 0]} maxBarSize={36} />
      </BarChart>
    </ResponsiveContainer>
  );
}
