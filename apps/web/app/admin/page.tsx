"use client";
import { useEffect, useState } from "react";
import CallMonitor from "@/components/CallMonitor";
import { get, post } from "@/lib/api";
import { useEvents } from "@/lib/sse";

function Tile({ label, value, sub }: { label: string; value: React.ReactNode; sub?: React.ReactNode }) {
  return <div className="card p-4"><div className="label">{label}</div><div className="mt-1 text-3xl font-bold tabular-nums">{value}</div>{sub && <div className="mt-0.5 text-xs text-ink-mute">{sub}</div>}</div>;
}

export default function Admin() {
  const [s, setS] = useState<any>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string>("");
  const [prog, setProg] = useState<any>(null);
  const [forceSim, setForceSim] = useState(false);
  const load = () => get("/api/stats").then(setS).catch(() => setMsg("Cannot reach the API on port 8000."));
  useEffect(() => { load(); const t = setInterval(load, 4000); return () => clearInterval(t); }, []);
  useEvents((ev) => { if (ev.type === "rematch_progress") setProg(ev.data); if (ev.type === "referral_created" || ev.type === "call_status") load(); });
  const run = async (name: string, fn: () => Promise<any>, ok: (r: any) => string) => {
    setBusy(name); setMsg("");
    try { setMsg(ok(await fn())); } catch (e: any) { setMsg(`Failed: ${e.message}`); }
    setBusy(null); load();
  };
  const j = s?.jev;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        <Tile label="UK trials loaded" value={s?.trials.count ?? "–"} sub={s ? `${s.trials.recruiting} recruiting · ${s.trials.criteria} atomic criteria · last update ${s.trials.last_update}` : ""} />
        <Tile label="Registry members" value={s?.registry_members ?? "–"} sub="consented to future contact" />
        <Tile label="Jev decisions" value={j?.decisions?.toLocaleString() ?? "–"} sub={j ? `${j.calls} requests · ${j.cache_hits} cache hits` : ""} />
        <Tile label="Jev latency p50" value={j?.p50_ms != null ? `${j.p50_ms} ms` : "–"} sub={j?.p95_ms != null ? `p95 ${j.p95_ms} ms per request` : "per request"} />
        <Tile label="Jev cost" value={j ? `$${j.cost_usd.toFixed(4)}` : "–"} sub={j ? `${j.model}${j.mock ? " · mock: tokens estimated" : ""} · vendor price` : ""} />
      </div>

      <div className="card p-4">
        <div className="label">Demo controls</div>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <button className="btn-primary !px-6 !py-3 text-base" disabled={!!busy}
            onClick={() => run("new", () => post("/api/demo/new-trial", { force_sim: forceSim }), (r) => `DEMO-001 opened: ${r.profiles} registry members re-checked with ${r.decisions} Jev decisions → ${r.referrals.length} referrals, ${r.calls.length} calls placed.`)}>
            {busy === "new" ? "Re-matching…" : "✚ Simulate new trial opening"}
          </button>
          <label className="flex items-center gap-2 text-sm text-ink-soft"><input type="checkbox" checked={forceSim} onChange={(e) => setForceSim(e.target.checked)} /> simulated calls only (no real phones)</label>
          <span className="flex-1" />
          <button className="btn-ghost" disabled={!!busy} onClick={() => run("ingest", () => post("/api/trials/ingest", { live: true }), (r) => `Ingest: ${r.trials} trials (${r.mode}).`)}>{busy === "ingest" ? "Fetching…" : "Re-ingest ClinicalTrials.gov"}</button>
          <button className="btn-ghost" disabled={!!busy} onClick={() => run("reset", () => post("/api/demo/reset"), (r) => `Demo reset in ${r.seconds}s: ${r.trials} trials, ${r.registry_members} registry members.`)}>{busy === "reset" ? "Resetting…" : "Reset demo state"}</button>
        </div>
        {prog && prog.total > 0 && (
          <div className="mt-3">
            <div className="flex justify-between text-xs text-ink-soft"><span>Re-matching registry against {prog.trial_ids?.join(", ")}</span><span className="tabular-nums">{prog.done}/{prog.total} · {prog.matched} matched</span></div>
            <div className="mt-1 h-2 overflow-hidden rounded bg-surface-sunk"><div className="h-full rounded bg-brand transition-all" style={{ width: `${(100 * prog.done) / prog.total}%` }} /></div>
          </div>
        )}
        {msg && <div className="mt-3 text-sm text-ink-soft">{msg}</div>}
        {s && (
          <div className="mt-3 flex flex-wrap gap-2 text-xs">
            <span className="chip">{s.voice.elevenlabs_intake_agent ? "✓" : "–"} ElevenLabs intake agent</span>
            <span className="chip">{s.voice.outbound_ready ? "✓" : "–"} Outbound phone calls (ElevenLabs + Twilio)</span>
            <span className="chip">{s.voice.tool_secret_set ? "✓" : "–"} Tool secret</span>
            <span className="chip">{s.voice.webhook_signed ? "✓" : "–"} Signed post-call webhook</span>
            {!s.voice.outbound_ready && <span className="text-ink-mute">Without phone credentials, calls are simulated and can be answered in the browser.</span>}
          </div>
        )}
      </div>
      <CallMonitor />
    </div>
  );
}
