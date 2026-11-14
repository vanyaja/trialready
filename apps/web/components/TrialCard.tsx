export default function TrialCard({ t }: { t: { trial_id: string; lay_title: string; lay_summary: string; band: string; site?: string | null; city?: string | null; distance_km?: number | null; to_check?: string[]; source?: string } }) {
  return (
    <div className="rounded-xl border border-surface-line bg-surface-sunk p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="font-semibold text-ink">{t.lay_title}</div>
        <span className={`chip shrink-0 ${t.band === "refer" ? "border-good/70" : "border-warn/60"}`}>{t.band === "refer" ? "✓ May be a good fit" : "◐ Nurse to check"}</span>
      </div>
      <p className="mt-1.5 text-sm text-ink-soft">{t.lay_summary}</p>
      <div className="mt-2 text-xs text-ink-mute">
        {t.source === "demo" ? "Demo trial (fictional)" : t.trial_id}{t.site ? ` · ${t.site}${t.city ? `, ${t.city}` : ""}` : ""}{t.distance_km != null ? ` · about ${Math.round(t.distance_km)} km away` : ""}
      </div>
      {!!t.to_check?.length && <div className="mt-2 text-xs text-ink-soft">Still to check: {t.to_check.join(" · ")}</div>}
    </div>
  );
}
