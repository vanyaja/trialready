"use client";
import { useEffect, useState } from "react";
import { get, post } from "@/lib/api";
import { useEvents } from "@/lib/sse";
import { listenOnce, recognitionAvailable, speak, stopSpeaking } from "@/lib/speech";
import Transcript, { Line } from "./Transcript";

type Call = { call_id: number; referral_id: number; status: string; mode: string; summary: string; person: string; lay_title: string; started_at?: string };
const STATUS: Record<string, [string, string]> = {
  queued: ["… queued", ""], dialing: ["☎ dialing", "border-brand"], ringing: ["☎ ringing", "border-warn/70"], in_progress: ["● in progress", "border-brand"],
  booked: ["✓ booked", "border-good/70"], not_eligible: ["✗ no longer eligible", "border-critical/70"], declined: ["– declined", ""], no_answer: ["– no answer", ""],
  completed: ["✓ completed", ""], failed: ["⚠ failed", "border-critical/70"], safety: ["⚠ safety stop", "border-critical"], withdrawn: ["– removed on request", ""],
};

function AnswerModal({ call, onClose }: { call: Call; onClose: () => void }) {
  const [lines, setLines] = useState<Line[]>([]);
  const [text, setText] = useState("");
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);
  const say = (s: string, d: boolean) => { setLines((p) => [...p, { role: "agent", text: s }]); setDone(d); speak(s, () => { if (!d && recognitionAvailable()) listenOnce(send); }); };
  const send = async (t: string) => {
    if (!t.trim() || busy) return;
    setText(""); setBusy(true); setLines((p) => [...p, { role: "caller", text: t }]);
    try { const r = await post(`/api/sim/calls/${call.call_id}/turn`, { text: t }); say(r.say, r.done); } catch (e: any) { setLines((p) => [...p, { role: "system", text: e.message }]); }
    setBusy(false);
  };
  useEffect(() => {
    post(`/api/sim/calls/${call.call_id}/answer`).then((r) => say(r.say, r.done)).catch(() => { setLines([{ role: "system", text: "This call is no longer ringing." }]); setDone(true); });
    return () => stopSpeaking();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
      <div className="card flex h-[70vh] w-full max-w-lg flex-col p-4">
        <div className="flex items-center justify-between">
          <div><div className="label">Simulated incoming call · {call.person}</div><div className="font-semibold">{call.lay_title}</div></div>
          <button className="btn-ghost" onClick={() => { stopSpeaking(); onClose(); }}>Close</button>
        </div>
        <Transcript lines={lines} className="mt-3 flex-1" />
        {!done && (
          <form className="mt-3 flex gap-2" onSubmit={(e) => { e.preventDefault(); send(text); }}>
            <input className="input" autoFocus placeholder="Answer as the person being called…" value={text} onChange={(e) => setText(e.target.value)} disabled={busy} />
            <button className="btn-primary" disabled={busy || !text.trim()}>Send</button>
          </form>
        )}
      </div>
    </div>
  );
}

export default function CallMonitor() {
  const [calls, setCalls] = useState<Call[]>([]);
  const [answering, setAnswering] = useState<Call | null>(null);
  const load = () => get("/api/calls?limit=20").then((r) => setCalls(r.calls)).catch(() => {});
  useEffect(() => { load(); }, []);
  useEvents((ev) => {
    if (ev.type !== "call_status") return;
    setCalls((prev) => {
      const i = prev.findIndex((c) => c.call_id === ev.data.call_id);
      if (i < 0) return [{ person: "…", lay_title: "", summary: "", ...ev.data }, ...prev];
      const next = [...prev];
      next[i] = { ...next[i], ...ev.data, person: ev.data.person ?? next[i].person, lay_title: ev.data.lay_title ?? next[i].lay_title };
      return next;
    });
  });
  return (
    <div className="card p-4">
      <div className="flex items-center justify-between"><div className="label">Outbound call monitor</div><button className="btn-ghost !py-1 text-xs" onClick={load}>Refresh</button></div>
      {calls.length === 0 && <div className="py-8 text-center text-sm text-ink-mute">No calls yet. Open a new trial to phone the matching registry members.</div>}
      <div className="mt-2 divide-y divide-surface-line/60">
        {calls.map((c) => (
          <div key={c.call_id} className="flex items-center gap-3 py-2.5">
            <span className={`text-xl ${c.status === "ringing" || c.status === "dialing" ? "ringing" : ""}`}>{c.mode === "sim" ? "💻" : "📞"}</span>
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium">{c.person} <span className="font-normal text-ink-mute">· {c.lay_title}</span></div>
              <div className="truncate text-xs text-ink-mute">{c.mode === "sim" ? "simulated call" : "phone (ElevenLabs + Twilio)"}{c.summary ? ` · ${c.summary}` : ""}</div>
            </div>
            {c.mode === "sim" && c.status === "ringing" && <button className="btn-primary !py-1.5 text-xs" onClick={() => setAnswering(c)}>Answer in browser</button>}
            <span className={`chip ${STATUS[c.status]?.[1] ?? ""}`}>{STATUS[c.status]?.[0] ?? c.status}</span>
          </div>
        ))}
      </div>
      {answering && <AnswerModal call={answering} onClose={() => { setAnswering(null); load(); }} />}
    </div>
  );
}
