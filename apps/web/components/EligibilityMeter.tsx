"use client";
import { pct } from "@/lib/api";

export type TopTrial = { trial_id: string; lay_title: string; p_opt: number; p_pes: number; uncertainty: number; knocked_out: boolean; band: string; source: string;
  distance_km: number | null; site: string | null; knock_text?: string | null; n_screenable: number; n_confirmed: number; n_unknown: number; n_nurse?: number };

const BAND: Record<string, [string, string]> = { refer: ["✓ Refer", "border-good/70 text-ink"], human_review: ["◐ Nurse review", "border-warn/60 text-ink"], unconfirmed: ["◌ Not assessed", "text-ink-mute"], no_match: ["– No match", "text-ink-mute"] };

export default function EligibilityMeter({ total, funnel, top, selected, onSelect }: { total: number; funnel: number[]; top: TopTrial[]; selected?: string; onSelect: (id: string) => void }) {
  const live = top.filter((t) => !t.knocked_out).slice(0, 10);
  const out = top.filter((t) => t.knocked_out).slice(0, 4);
  const steps = [total, ...funnel];
  const current = steps[steps.length - 1];
  return (
    <div className="card flex h-full min-h-0 flex-col p-4">
      <div className="label">Trials still possible</div>
      <div className="mt-1 flex flex-wrap items-baseline gap-x-2 gap-y-1">
        {steps.slice(0, -1).slice(-5).map((n, i) => <span key={i} className="text-xl font-semibold text-ink-mute">{n} <span className="text-ink-mute/60">→</span></span>)}
        <span className="text-5xl font-bold tabular-nums text-ink">{current}</span>
        <span className="text-sm text-ink-mute">of {total} UK studies</span>
      </div>
      <div className="mt-3 flex items-center gap-4 text-xs text-ink-soft">
        <span className="flex items-center gap-1.5"><span className="h-2.5 w-5 rounded-sm bg-brand/30" /> still possible (P_opt)</span>
        <span className="flex items-center gap-1.5"><span className="h-2.5 w-5 rounded-sm bg-brand" /> confirmed so far (P_pes)</span>
      </div>
      <div className="scroll-thin mt-2 min-h-0 flex-1 space-y-1.5 overflow-y-auto pr-1">
        {live.length === 0 && <div className="py-8 text-center text-sm text-ink-mute">Scores appear after the first answer.</div>}
        {live.map((t) => (
          <button key={t.trial_id} onClick={() => onSelect(t.trial_id)}
            title={`${t.lay_title}\nStill possible ${pct(t.p_opt)} · confirmed ${pct(t.p_pes)}\n${t.n_confirmed} of ${t.n_screenable} phone-checkable criteria confirmed, ${t.n_unknown} still to ask, ${t.n_nurse ?? 0} for the nurse`}
            className={`block w-full rounded-lg border px-3 py-2 text-left transition ${selected === t.trial_id ? "border-brand bg-brand-soft/40" : "border-transparent hover:border-surface-line"}`}>
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-sm font-medium text-ink">{t.source === "demo" && <span className="mr-1.5 rounded bg-warn/20 px-1 text-[0.65rem] font-bold text-warn">DEMO</span>}{t.lay_title}</span>
              <span className={`chip shrink-0 ${BAND[t.band]?.[1]}`}>{BAND[t.band]?.[0]}</span>
            </div>
            <div className="relative mt-1.5 h-2.5 overflow-hidden rounded bg-surface-sunk">
              <div className="absolute inset-y-0 left-0 rounded bg-brand/30 transition-all duration-500" style={{ width: `${t.p_opt * 100}%` }} />
              <div className="absolute inset-y-0 left-0 rounded bg-brand transition-all duration-500" style={{ width: `${t.p_pes * 100}%` }} />
            </div>
            <div className="mt-1 flex justify-between text-[0.7rem] tabular-nums text-ink-mute">
              <span>{pct(t.p_opt)} possible · {pct(t.p_pes)} confirmed</span>
              <span>{t.n_confirmed}/{t.n_screenable} confirmed{t.n_unknown ? ` · ${t.n_unknown} to ask` : ""}{t.distance_km != null ? ` · ${Math.round(t.distance_km)} km` : ""}</span>
            </div>
          </button>
        ))}
        {out.length > 0 && <div className="label pt-2">Ruled out</div>}
        {out.map((t) => (
          <button key={t.trial_id} onClick={() => onSelect(t.trial_id)} className="block w-full rounded-lg px-3 py-1 text-left text-xs text-ink-mute hover:bg-surface-line/30">
            <span className="line-through">{t.lay_title}</span>{t.knock_text ? <span> — ✗ {t.knock_text}</span> : null}
          </button>
        ))}
      </div>
    </div>
  );
}
