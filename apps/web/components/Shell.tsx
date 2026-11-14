"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { get } from "@/lib/api";

const NAV = [["/call", "Call"], ["/admin", "Admin"], ["/site", "Site"], ["/registry", "Registry"], ["/equity", "Equity"], ["/benchmark", "Benchmark"]];

export function JevBadge({ jev }: { jev?: { active: string; mock: boolean; fallback_mode: boolean; model?: string } }) {
  if (!jev) return null;
  if (jev.mock) return <span className="chip border-warn/60 text-warn" title="No Jev key configured, or every provider failed: decisions come from keyword rules">⚠ Jev: mock rules</span>;
  if (jev.fallback_mode) return <span className="chip border-warn/60 text-warn" title={jev.model}>⚠ Jev fallback: {jev.active}</span>;
  return <span className="chip border-good/60 text-ink" title={jev.model}><span className="h-2 w-2 rounded-full bg-good" /> Jev: {jev.active}</span>;
}

export default function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const [stage, setStage] = useState(false);
  const [jev, setJev] = useState<any>();
  useEffect(() => { setStage(localStorage.getItem("stage") === "1"); }, []);
  useEffect(() => {
    document.documentElement.classList.toggle("stage", stage);
    localStorage.setItem("stage", stage ? "1" : "0");
  }, [stage]);
  useEffect(() => {
    const load = () => get("/api/config").then((c) => setJev(c.jev)).catch(() => setJev(undefined));
    load();
    const t = setInterval(load, 8000);
    return () => clearInterval(t);
  }, []);
  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-30 border-b border-surface-line bg-surface/90 backdrop-blur">
        <div className="mx-auto flex max-w-[1500px] items-center gap-5 px-5 py-2.5">
          <Link href="/call" className="text-lg font-bold tracking-tight">Trial<span className="text-brand">Ready</span></Link>
          <nav className="flex flex-1 gap-1">
            {NAV.map(([href, label]) => (
              <Link key={href} href={href} className={`rounded-md px-3 py-1.5 text-sm font-medium ${path?.startsWith(href) ? "bg-brand-soft text-ink" : "text-ink-mute hover:text-ink"}`}>{label}</Link>
            ))}
          </nav>
          <JevBadge jev={jev} />
          <button className="btn-ghost !px-3 !py-1.5" onClick={() => setStage((s) => !s)} title="Bigger type for the projector">{stage ? "Stage mode: on" : "Stage mode"}</button>
        </div>
      </header>
      <main className="mx-auto w-full max-w-[1500px] flex-1 px-5 py-5">{children}</main>
      <footer className="border-t border-surface-line px-5 py-2.5 text-center text-xs text-ink-mute">
        Pre-screening only · not medical advice · synthetic &amp; consented data only · a research nurse always confirms eligibility
      </footer>
    </div>
  );
}
