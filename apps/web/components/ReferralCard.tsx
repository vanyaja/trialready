"use client";
import { pct } from "@/lib/api";

const STATUS: Record<string, [string, string]> = {
  new: ["● new", "border-brand"], booked: ["✓ booked", "border-good/70"], accepted: ["✓ accepted", "border-good/70"],
  needs_info: ["? needs info", "border-warn/60"], ineligible: ["✗ ineligible", "border-critical/70"],
};

export function StatusChip({ status }: { status: string }) {
  return <span className={`chip ${STATUS[status]?.[1] ?? ""}`}>{STATUS[status]?.[0] ?? status}</span>;
}

export default function ReferralCard({ r, selected, onClick }: { r: any; selected: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick} className={`block w-full rounded-xl border p-3 text-left transition ${selected ? "border-brand bg-brand-soft/40" : "border-surface-line bg-surface-raised hover:border-ink-mute"}`}>
      <div className="flex items-center justify-between gap-2">
        <div className="truncate text-sm font-semibold">{r.person.pseudonym}<span className="font-normal text-ink-mute"> · {r.person.age_band ?? "age ?"} · {r.person.district ?? "?"}{r.person.synthetic ? " · synthetic" : ""}</span></div>
        <StatusChip status={r.status} />
      </div>
      <div className="mt-0.5 truncate text-sm text-ink-soft">{r.trial.source === "demo" && <span className="mr-1 rounded bg-warn/20 px-1 text-[0.65rem] font-bold text-warn">DEMO</span>}{r.trial.lay_title || r.trial.title}</div>
      <div className="relative mt-2 h-2 overflow-hidden rounded bg-surface-sunk" title={`P_opt ${pct(r.p_opt)} · P_pes ${pct(r.p_pes)}`}>
        <div className="absolute inset-y-0 left-0 rounded bg-brand/30" style={{ width: `${r.p_opt * 100}%` }} />
        <div className="absolute inset-y-0 left-0 rounded bg-brand" style={{ width: `${r.p_pes * 100}%` }} />
      </div>
      <div className="mt-1 flex justify-between text-[0.7rem] text-ink-mute">
        <span>{r.band === "refer" ? "Refer" : "Human review"} · via {r.source}</span>
        <span className="tabular-nums">✓ {r.counts.ok} · ✗ {r.counts.problem} · ? {r.counts.unknown}{r.slot ? ` · ${r.slot.spoken}` : ""}</span>
      </div>
    </button>
  );
}
