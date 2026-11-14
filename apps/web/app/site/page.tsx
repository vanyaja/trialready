"use client";
import { useEffect, useState } from "react";
import CriteriaTable from "@/components/CriteriaTable";
import ReferralCard, { StatusChip } from "@/components/ReferralCard";
import { get, pct, post } from "@/lib/api";
import { useEvents } from "@/lib/sse";

export default function Site() {
  const [list, setList] = useState<any[]>([]);
  const [meta, setMeta] = useState<any>({});
  const [sel, setSel] = useState<number | null>(null);
  const [d, setD] = useState<any>(null);
  const [reason, setReason] = useState("");
  const [filter, setFilter] = useState("all");
  const load = () => get("/api/site/referrals").then((r) => { setList(r.referrals); setMeta(r); }).catch(() => {});
  const open = (id: number) => { setSel(id); get(`/api/site/referrals/${id}`).then(setD).catch(() => setD(null)); };
  useEffect(() => { load(); }, []);
  useEvents((ev) => { if (ev.type === "referral_created" || ev.type === "referral_updated") { load(); if (sel && ev.data.referral_id === sel) open(sel); } });
  const decide = async (decision: string) => { if (!sel) return; setD(await post(`/api/site/referrals/${sel}/decision`, { decision, reason })); setReason(""); load(); };
  const shown = list.filter((r) => filter === "all" || (filter === "human_review" ? r.band === "human_review" && r.status === "new" : r.status === filter));
  return (
    <div className="grid grid-cols-12 gap-4 lg:h-[calc(100vh-7.6rem)] lg:min-h-[560px] lg:grid-rows-[minmax(0,1fr)]">
      <section className="col-span-12 flex min-h-0 flex-col lg:col-span-4">
        <div className="mb-2 flex items-center gap-2">
          <div className="label mr-auto">Referral queue · {list.length}</div>
          {["all", "new", "booked", "human_review"].map((f) => <button key={f} className={`chip ${filter === f ? "border-brand text-ink" : ""}`} onClick={() => setFilter(f)}>{f.replace("_", " ")}</button>)}
        </div>
        <div className="scroll-thin min-h-0 flex-1 space-y-2 overflow-y-auto pr-1">
          {shown.length === 0 && <div className="card p-8 text-center text-sm text-ink-mute">No referrals yet. Finish an intake call, or open a new trial from Admin.</div>}
          {shown.map((r) => <ReferralCard key={r.id} r={r} selected={sel === r.id} onClick={() => open(r.id)} />)}
        </div>
        <div className="card mt-3 p-3 text-xs text-ink-soft"><span className="label">Outcome loop</span><div className="mt-1"><b className="text-ink">{meta.outcomes_logged ?? 0}</b> nurse outcomes logged. {meta.outcome_loop_note}</div></div>
      </section>
      <section className="card col-span-12 flex min-h-0 flex-col overflow-hidden p-4 lg:col-span-8">
        {!d ? <div className="m-auto text-sm text-ink-mute">Select a referral to see the evidence.</div> : (
          <>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="label">Referral #{d.id} · {d.source} · {d.band === "refer" ? "refer" : "human review"}</div>
                <div className="text-lg font-semibold">{d.person.pseudonym} → {d.trial.lay_title || d.trial.title}</div>
                <div className="text-xs text-ink-mute">{d.trial.source === "demo" ? "Demo trial (fictional)" : d.trial.id} · {d.person.age_band ?? "age ?"} · {d.person.sex ?? "sex ?"} · {d.person.district ?? "district ?"} · language {d.person.language}{d.person.synthetic ? " · synthetic persona" : ""}</div>
              </div>
              <div className="text-right"><StatusChip status={d.status} /><div className="mt-1 text-xs tabular-nums text-ink-mute">confirmed {pct(d.p_pes)} · possible {pct(d.p_opt)}</div></div>
            </div>
            {d.slot && <div className="mt-2 rounded-lg border border-good/50 bg-good/10 px-3 py-2 text-sm">Booked: <b>{d.slot.spoken}</b> at {d.slot.site_name}</div>}
            <div className="mt-3 grid min-h-0 flex-1 grid-cols-12 gap-4 xl:grid-rows-[minmax(0,1fr)]">
              <div className="col-span-12 flex min-h-0 flex-col xl:col-span-8"><CriteriaTable rows={d.evidence} /></div>
              <div className="scroll-thin col-span-12 min-h-0 space-y-3 overflow-y-auto text-sm xl:col-span-4">
                <div><div className="label">Transcript (answers)</div>
                  {d.transcript.map((t: any, i: number) => <div key={i} className="mt-1.5"><div className="text-xs text-ink-mute">{t.call === "recall" ? "☎ recall · " : ""}{t.q}</div><div className="text-ink">“{t.a}”</div></div>)}
                </div>
                {d.calls.length > 0 && <div><div className="label">Calls</div>{d.calls.map((c: any) => <div key={c.id} className="mt-1 text-xs text-ink-soft">{c.mode} · {c.status} · {c.summary}</div>)}</div>}
                {d.outcomes.length > 0 && <div><div className="label">Outcomes</div>{d.outcomes.map((o: any, i: number) => <div key={i} className="mt-1 text-xs text-ink-soft">{o.decision}{o.reason ? ` — ${o.reason}` : ""}</div>)}</div>}
              </div>
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-surface-line pt-3">
              <input className="input flex-1" placeholder="Reason / note (optional)" value={reason} onChange={(e) => setReason(e.target.value)} />
              <button className="btn-primary" onClick={() => decide("accepted")}>✓ Accept</button>
              <button className="btn-ghost" onClick={() => decide("needs_info")}>? Needs info</button>
              <button className="btn-danger" onClick={() => decide("ineligible")}>✗ Ineligible</button>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
