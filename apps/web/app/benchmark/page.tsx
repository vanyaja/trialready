"use client";
import { useEffect, useState } from "react";
import { LatencyChart, ReliabilityChart } from "@/components/CalibrationChart";
import { get, pct } from "@/lib/api";

function Tile({ label, value, sub }: { label: string; value: React.ReactNode; sub?: string }) {
  return <div className="card p-4"><div className="label">{label}</div><div className="mt-1 text-3xl font-bold tabular-nums">{value}</div>{sub && <div className="mt-0.5 text-xs text-ink-mute">{sub}</div>}</div>;
}

export default function Benchmark() {
  const [d, setD] = useState<any>(null);
  const [map, setMap] = useState("drop_not_applicable");
  useEffect(() => { get("/api/benchmark").then(setD).catch(() => setD({ available: false, hint: "Cannot reach the API." })); }, []);
  if (!d) return <div className="text-sm text-ink-mute">Loading…</div>;
  if (!d.available) return <div className="card p-6 text-sm text-ink-soft">No benchmark results yet. {d.hint}</div>;
  const m = d.mappings[map];
  const labels = ["met", "not_met", "unknown"];
  return (
    <div className="space-y-4">
      {d.mock && <div className="rounded-xl border-2 border-critical bg-critical/15 p-4 text-sm font-semibold">⚠ {d.warning} Re-run with a Jev key: <code className="font-normal">python -m services.api.benchmark.run_jev_benchmark --n 500</code></div>}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="label">Benchmark</div>
          <h1 className="text-2xl font-bold">Jev vs expert criterion annotations</h1>
          <p className="text-sm text-ink-soft">{d.dataset.name}: {d.dataset.answered} of {d.dataset.total_pairs} physician-labelled patient–criterion pairs ({d.dataset.sampling}). Model: {d.model}.</p>
        </div>
        <div className="flex gap-1.5">
          {Object.keys(d.mappings).map((k) => <button key={k} className={`chip !px-3 !py-1 ${map === k ? "border-brand text-ink" : ""}`} onClick={() => setMap(k)} title={d.mappings[k].description}>{k === "drop_not_applicable" ? "‘not applicable’ dropped" : "‘not applicable’ → unknown"}</button>)}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-6">
        <Tile label="Accuracy" value={pct(m.accuracy, 1)} sub={`n = ${m.n}`} />
        <Tile label="Inclusion / exclusion" value={`${pct(m.accuracy_inclusion)} / ${pct(m.accuracy_exclusion)}`} sub={`${m.n_inclusion} / ${m.n_exclusion} pairs`} />
        <Tile label="Macro-F1" value={m.macro_f1?.toFixed(3) ?? "–"} sub="met / not met / unknown" />
        <Tile label="Calibration (ECE)" value={m.ece?.toFixed(3) ?? "–"} sub="10 bins · lower is better" />
        <Tile label="Latency p50 / p95" value={`${d.latency.request_p50_ms ?? "–"} / ${d.latency.request_p95_ms ?? "–"} ms`} sub={`per request · ${d.latency.per_decision_p50_ms ?? "–"} ms per decision`} />
        <Tile label="Cost per 1,000 criteria" value={`$${d.cost.usd_per_1000_criteria}`} sub={d.cost.label} />
      </div>
      <div className="card p-4">
        <div className="font-semibold">Comparison</div>
        <table className="mt-2 w-full text-left text-sm">
          <thead><tr className="label border-b border-surface-line"><th className="py-1.5">System</th><th className="text-right">Accuracy</th><th className="text-right">Macro-F1</th><th className="text-right">ECE</th><th className="text-right">Pairs</th><th className="pl-4">Provenance</th></tr></thead>
          <tbody>{d.comparison.map((c: any, i: number) => (
            <tr key={i} className="border-b border-surface-line/50 tabular-nums"><td className="py-1.5 font-medium">{c.label}</td><td className="text-right">{pct(c.accuracy, 1)}</td><td className="text-right">{c.macro_f1?.toFixed(3) ?? "–"}</td>
              <td className="text-right">{c.ece?.toFixed(3) ?? "–"}</td><td className="text-right">{c.n}</td><td className="pl-4 text-xs text-ink-mute">{c.rerun ? "run here" : (c.note ?? "not re-run")}</td></tr>))}</tbody>
        </table>
      </div>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <div className="card p-4"><div className="font-semibold">Reliability diagram</div><div className="text-xs text-ink-mute">Accuracy against stated confidence; the dashed diagonal is perfect calibration.</div><ReliabilityChart bins={m.reliability} /></div>
        <div className="card p-4"><div className="font-semibold">Request latency</div><div className="text-xs text-ink-mute">{d.latency.requests} requests, up to 50 criteria each.</div><LatencyChart hist={d.latency.histogram} /></div>
        <div className="card p-4">
          <div className="font-semibold">Confusion matrix</div><div className="text-xs text-ink-mute">Rows: expert label. Columns: model.</div>
          <table className="mt-3 w-full text-center text-sm tabular-nums">
            <thead><tr><th />{labels.map((l) => <th key={l} className="label pb-1">{l.replace("_", " ")}</th>)}</tr></thead>
            <tbody>{labels.map((g) => (
              <tr key={g}><th className="label pr-2 text-right">{g.replace("_", " ")}</th>
                {labels.map((p) => { const v = m.confusion[g][p]; const rowMax = Math.max(1, ...labels.map((x) => m.confusion[g][x]));
                  return <td key={p} className="border border-surface-line p-2.5" style={{ background: `rgba(57,135,229,${0.08 + 0.6 * (v / rowMax)})` }}><span className={g === p ? "font-bold text-ink" : "text-ink"}>{v}</span></td>; })}
              </tr>))}</tbody>
          </table>
          <div className="mt-4 text-sm text-ink-soft">
            <div className="label">Threshold from this run</div>
            <p className="mt-1">P(met) cut-off for ≥ {d.threshold.target_precision * 100}% precision on “met”: <b className="text-ink">{d.threshold.met_cutoff ?? "not reached"}</b>. {d.threshold.note}</p>
            <p className="mt-1 text-xs text-ink-mute">In use: knock-out {d.thresholds_in_use.KNOCKOUT} · refer when P_pes ≥ {d.thresholds_in_use.REFER_P_PES} · nurse review when P_opt ≥ {d.thresholds_in_use.REVIEW_P_OPT}.</p>
          </div>
        </div>
      </div>
    </div>
  );
}
