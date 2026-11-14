"use client";
import { useEffect, useRef } from "react";

export type Line = { role: "agent" | "caller" | "system"; text: string };

export default function Transcript({ lines, className = "" }: { lines: Line[]; className?: string }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => { box.current?.scrollTo({ top: box.current.scrollHeight, behavior: "smooth" }); }, [lines.length]);
  return (
    <div ref={box} className={`scroll-thin min-h-0 space-y-2 overflow-y-auto pr-1 ${className}`}>
      {lines.length === 0 && <div className="py-10 text-center text-sm text-ink-mute">The conversation will appear here.</div>}
      {lines.map((l, i) => l.role === "system" ? (
        <div key={i} className="text-center text-xs text-ink-mute">{l.text}</div>
      ) : (
        <div key={i} className={`flex ${l.role === "caller" ? "justify-end" : "justify-start"}`}>
          <div className={`max-w-[88%] rounded-2xl px-3.5 py-2 text-sm leading-snug ${l.role === "caller" ? "rounded-br-sm bg-brand text-white" : "rounded-bl-sm bg-surface-sunk text-ink"}`}>
            <div className={`mb-0.5 text-[0.62rem] font-semibold uppercase tracking-wider ${l.role === "caller" ? "text-white/70" : "text-ink-mute"}`}>{l.role === "caller" ? "Caller" : "TrialReady (AI)"}</div>
            {l.text}
          </div>
        </div>
      ))}
    </div>
  );
}
