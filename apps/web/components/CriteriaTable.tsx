"use client";
import { pct } from "@/lib/api";

export type CritRow = { criterion_id: string; kind: string; topic: string; text: string; lay_text: string; status: string; confidence: number; ok: boolean | null;
  evidence?: string | null; screenable: boolean; pending?: boolean; hard_filter?: boolean; cohort_specific?: boolean };

function Status({ r }: { r: CritRow }) {
  const label = r.status === "met" ? (r.kind === "exclusion" ? "applies" : "met") : r.status === "not_met" ? (r.kind === "exclusion" ? "does not apply" : "not met") : "unknown";
  if (r.ok === true) return <span className="chip border-good/70 text-ink">✓ {label}</span>;
  if (r.ok === false) return <span className="chip border-critical/80 text-ink">✗ {label}</span>;
  return <span className="chip text-ink-mute">? {r.pending ? "not asked yet" : "for the nurse"}</span>;
}

export default function CriteriaTable({ rows, compact = false }: { rows: CritRow[]; compact?: boolean }) {
  if (!rows?.length) return <div className="py-6 text-center text-sm text-ink-mute">No criteria to show yet.</div>;
  const order = (r: CritRow) => (r.ok === false ? 0 : r.ok === true ? 1 : r.pending ? 2 : 3);
  const sorted = [...rows].sort((a, b) => order(a) - order(b));
  return (
    <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">
      <table className="w-full text-left text-sm">
        <thead className="sticky top-0 bg-surface-raised">
          <tr className="label border-b border-surface-line"><th className="py-1.5 pr-2">Criterion</th><th className="px-2">Decision</th><th className="px-2 text-right">Conf.</th>{!compact && <th className="pl-2">Evidence (caller&apos;s words)</th>}</tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={r.criterion_id} className="border-b border-surface-line/50 align-top">
              <td className="py-1.5 pr-2" title={r.text}>
                <span className={`mr-1.5 rounded px-1 text-[0.62rem] font-bold uppercase ${r.kind === "inclusion" ? "bg-brand-soft text-ink" : "bg-surface-line text-ink-soft"}`}>{r.kind === "inclusion" ? "need" : "must not"}</span>
                <span className={r.ok === null ? "text-ink-soft" : "text-ink"}>{r.lay_text}</span>
                {r.cohort_specific && <span className="ml-1 text-[0.65rem] text-ink-mute">(one cohort only)</span>}
              </td>
              <td className="whitespace-nowrap px-2 py-1.5"><Status r={r} /></td>
              <td className="px-2 py-1.5 text-right tabular-nums text-ink-soft">{pct(r.confidence)}</td>
              {!compact && <td className="py-1.5 pl-2 text-xs italic text-ink-soft">{r.evidence && r.ok !== null ? `“${r.evidence}”` : ""}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
