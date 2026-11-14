"use client";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

export const SERIES = { registry: "#3987e5", census: "#d95926" }; // validated pair for the dark surface (dataviz palette slots 1-2)
const INK = "#8591a8";

export function Tip({ active, payload, label, unit = "%" }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-surface-line bg-surface-sunk px-3 py-2 text-xs shadow-lg">
      <div className="mb-1 font-semibold text-ink">{label}</div>
      {payload.map((p: any) => (
        <div key={p.dataKey} className="flex items-center gap-2 text-ink-soft"><span className="h-2 w-2 rounded-sm" style={{ background: p.color }} />{p.name}: <b className="tabular-nums text-ink">{p.value}{unit}</b></div>
      ))}
    </div>
  );
}

export default function EquityChart({ data, xKey, area }: { data: any[]; xKey: string; area: string }) {
  return (
    <ResponsiveContainer width="100%" height={300}>
      <BarChart data={data} barGap={2} barCategoryGap="28%" margin={{ top: 8, right: 8, left: -8, bottom: 0 }}>
        <CartesianGrid stroke="#26334f" strokeDasharray="2 4" vertical={false} />
        <XAxis dataKey={xKey} tick={{ fill: INK, fontSize: 12 }} axisLine={{ stroke: "#26334f" }} tickLine={false} />
        <YAxis tick={{ fill: INK, fontSize: 12 }} axisLine={false} tickLine={false} unit="%" />
        <Tooltip content={<Tip />} cursor={{ fill: "#26334f", opacity: 0.35 }} />
        <Legend wrapperStyle={{ fontSize: 12, color: "#c3c7d1" }} iconType="square" formatter={(v) => <span style={{ color: "#c3c7d1" }}>{v}</span>} />
        <Bar dataKey="registry_pct" name="TrialReady registry" fill={SERIES.registry} radius={[4, 4, 0, 0]} maxBarSize={26} />
        <Bar dataKey="census_pct" name={`Census 2021, ${area}`} fill={SERIES.census} radius={[4, 4, 0, 0]} maxBarSize={26} />
      </BarChart>
    </ResponsiveContainer>
  );
}
