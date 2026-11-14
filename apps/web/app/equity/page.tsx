"use client";
import { useEffect, useState } from "react";
import EquityChart from "@/components/EquityChart";
import { get } from "@/lib/api";

export default function Equity() {
  const [area, setArea] = useState("camden");
  const [d, setD] = useState<any>(null);
  useEffect(() => { get(`/api/equity?area=${area}`).then(setD).catch(() => setD(null)); }, [area]);
  if (!d) return <div className="text-sm text-ink-mute">Loading… (is the API running?)</div>;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="label">Equity view</div>
          <h1 className="text-2xl font-bold">Who is missing from the registry?</h1>
          <p className="text-sm text-ink-soft">{d.registry_n} registry members ({d.ethnicity_answered} gave an ethnic group) compared with the Census 2021 population of {d.area}.</p>
        </div>
        <div className="flex gap-1.5">{["camden", "london"].map((a) => <button key={a} className={`chip !px-3 !py-1 ${area === a ? "border-brand text-ink" : ""}`} onClick={() => setArea(a)}>{a === "camden" ? "Camden" : "London"}</button>)}</div>
      </div>
      {d.missing.length > 0 && <div className="card border-warn/50 p-3 text-sm"><b>Under-represented in this sample:</b> {d.missing.join(", ")} <span className="text-ink-mute">(registry share below 80% of the Census share)</span></div>}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <div className="card p-4">
          <div className="font-semibold">Ethnic group: registry vs Census (% of people)</div>
          <EquityChart data={d.ethnicity} xKey="group" area={d.area} />
          <table className="mt-2 w-full text-left text-sm">
            <thead><tr className="label border-b border-surface-line"><th className="py-1">Group</th><th className="text-right">Registry</th><th className="text-right">Census</th><th className="text-right">Gap</th><th className="pl-3">Flag</th></tr></thead>
            <tbody>{d.ethnicity.map((e: any) => (
              <tr key={e.group} className="border-b border-surface-line/50 tabular-nums"><td className="py-1">{e.group}</td><td className="text-right">{e.registry_pct}% <span className="text-ink-mute">({e.registry_n})</span></td><td className="text-right">{e.census_pct}%</td>
                <td className="text-right">{e.gap_pp > 0 ? "+" : ""}{e.gap_pp} pp</td><td className="pl-3">{e.under_represented ? <span className="chip border-warn/60">▼ under-represented</span> : ""}</td></tr>))}</tbody>
          </table>
        </div>
        <div className="card p-4">
          <div className="font-semibold">Age: registry vs Census residents aged 20+ (% of people)</div>
          <EquityChart data={d.age} xKey="band" area={d.area} />
          <table className="mt-2 w-full text-left text-sm">
            <thead><tr className="label border-b border-surface-line"><th className="py-1">Age band</th><th className="text-right">Registry</th><th className="text-right">Census</th><th className="text-right">Gap</th></tr></thead>
            <tbody>{d.age.map((e: any) => (
              <tr key={e.band} className="border-b border-surface-line/50 tabular-nums"><td className="py-1">{e.band}</td><td className="text-right">{e.registry_pct}% <span className="text-ink-mute">({e.registry_n})</span></td><td className="text-right">{e.census_pct}%</td><td className="text-right">{e.gap_pp > 0 ? "+" : ""}{e.gap_pp} pp</td></tr>))}</tbody>
          </table>
          <div className="mt-3 text-sm text-ink-soft">Languages used: {d.languages.map((l: any) => `${l.language} (${l.n})`).join(" · ") || "–"}</div>
        </div>
      </div>
      <p className="text-xs text-ink-mute">{d.note} Source: {d.source}</p>
    </div>
  );
}
