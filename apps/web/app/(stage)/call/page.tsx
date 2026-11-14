"use client";
import dynamic from "next/dynamic";
import { useCallback, useEffect, useRef, useState } from "react";
import CriteriaTable, { CritRow } from "@/components/CriteriaTable";
import EligibilityMeter, { TopTrial } from "@/components/EligibilityMeter";
import Transcript, { Line } from "@/components/Transcript";
import TrialCard from "@/components/TrialCard";
import { get, post } from "@/lib/api";
import { useEvents } from "@/lib/sse";
import { listenOnce, recognitionAvailable, speak, stopSpeaking } from "@/lib/speech";

const ElevenLabsCall = dynamic(() => import("@/components/ElevenLabsCall"), { ssr: false });
type Mode = "builtin" | "elevenlabs" | "watch";

export default function CallPage() {
  const [cfg, setCfg] = useState<any>(null);
  const [mode, setMode] = useState<Mode>("builtin");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [active, setActive] = useState(false);
  const [lines, setLines] = useState<Line[]>([]);
  const [total, setTotal] = useState(0);
  const [funnel, setFunnel] = useState<number[]>([]);
  const [top, setTop] = useState<TopTrial[]>([]);
  const [selected, setSelected] = useState<string | undefined>();
  const [detail, setDetail] = useState<{ trial: any; criteria: CritRow[] } | null>(null);
  const [topic, setTopic] = useState<string | null>(null);
  const [safety, setSafety] = useState<string | null>(null);
  const [end, setEnd] = useState<any>(null);
  const [consent, setConsent] = useState<{ screening?: boolean; future_contact?: boolean | null }>({});
  const [jev, setJev] = useState<any>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [voiceOn, setVoiceOn] = useState(true);
  const [listening, setListening] = useState(false);
  const [elStatus, setElStatus] = useState("disconnected");
  const [canListen, setCanListen] = useState(false);
  useEffect(() => { setCanListen(recognitionAvailable()); }, []);
  const sid = useRef<string | null>(null);
  const pinned = useRef(false);
  sid.current = sessionId;

  useEffect(() => {
    get("/api/config").then((c) => { setCfg(c); if (c.elevenlabs_intake_agent_id) setMode("elevenlabs"); }).catch(() => setCfg({ offline: true }));
    get("/api/trials").then((t) => setTotal(t.count)).catch(() => {});
  }, []);

  const add = useCallback((l: Line) => setLines((p) => [...p, l]), []);
  const reset = () => { setLines([]); setFunnel([]); setTop([]); setSelected(undefined); setDetail(null); setTopic(null); setSafety(null); setEnd(null); setConsent({}); setJev(null); pinned.current = false; };

  const loadDetail = useCallback((s: string, tid: string) => {
    get(`/api/sessions/${s}/trial/${tid}`).then(setDetail).catch(() => setDetail(null));
  }, []);

  const connected = useEvents((ev) => {
    if (ev.type === "session_started" && mode === "watch" && ev.data.channel !== "sim" && ev.data.channel !== "web") {
      reset(); setSessionId(ev.data.session_id); setActive(true); setTotal(ev.data.trials_total);
      add({ role: "system", text: `Incoming ${ev.data.channel === "phone_in" ? "phone" : ev.data.channel} call` });
      return;
    }
    if (!sid.current || ev.session_id !== sid.current) return;
    const d = ev.data;
    if (ev.type === "consent") setConsent((c) => ({ ...c, ...d }));
    if (ev.type === "next_question") { setTopic(d.topic); if (mode === "watch") add({ role: "agent", text: d.question }); }
    if (ev.type === "turn_scored") {
      setFunnel(d.funnel); setTop(d.top); setTotal(d.trials_total); setJev(d.jev);
      if (mode === "watch") add({ role: "caller", text: d.turn.answer });
      const live = (d.top as TopTrial[]).filter((t) => !t.knocked_out);
      const keep = pinned.current && selected && d.top.some((t: TopTrial) => t.trial_id === selected);
      const pick = keep ? selected! : live[0]?.trial_id;
      if (pick) { setSelected(pick); loadDetail(sid.current, pick); }
      if (d.done) setTopic(null);
    }
    if (ev.type === "safety") { setSafety(d.message); setActive(false); }
    if (ev.type === "withdrawn") { add({ role: "system", text: "Caller asked to be removed: profile deleted." }); setConsent({ screening: false, future_contact: false }); }
    if (ev.type === "screening_done") { setEnd(d); if (mode === "watch") add({ role: "agent", text: d.spoken_summary }); }
  });

  const agentSays = useCallback((say: string, done: boolean) => {
    add({ role: "agent", text: say });
    const after = () => { if (!done && voiceOn && recognitionAvailable()) listenOnce((t) => send(t), setListening); };
    if (voiceOn) speak(say, after);
  }, [voiceOn]); // eslint-disable-line react-hooks/exhaustive-deps

  const start = async () => {
    reset(); stopSpeaking();
    try {
      const s = await post("/api/sessions", { channel: "web", language: "en" });
      setSessionId(s.session_id); sid.current = s.session_id; setActive(true);
      if (mode === "builtin") agentSays(s.first_message, false);
    } catch (e: any) { add({ role: "system", text: `Cannot reach the API (${e.message}). Is the backend running on port 8000?` }); }
  };

  const send = async (t: string) => {
    const msg = t.trim();
    if (!msg || !sid.current || busy) return;
    setText(""); add({ role: "caller", text: msg }); setBusy(true);
    try {
      const r = await post("/api/sim/turn", { session_id: sid.current, text: msg });
      agentSays(r.say, r.done);
      if (r.done) setActive(false);
    } catch (e: any) { add({ role: "system", text: `Error: ${e.message}` }); }
    setBusy(false);
  };

  const hangUp = () => { stopSpeaking(); setActive(false); add({ role: "system", text: "Call ended." }); };
  const select = (id: string) => { pinned.current = true; setSelected(id); if (sid.current) loadDetail(sid.current, id); };
  const hasAgent = !!cfg?.elevenlabs_intake_agent_id;

  return (
    <div className="grid grid-cols-12 gap-4 lg:h-[calc(100vh-7.6rem)] lg:min-h-[560px] lg:grid-rows-[minmax(0,1fr)]">
      {/* left: call + transcript */}
      <section className="card col-span-12 flex min-h-0 flex-col p-4 max-lg:h-[70vh] lg:col-span-4">
        <div className="flex flex-wrap items-center gap-2">
          <div className="label mr-auto">Live call {active && <span className="live-dot ml-1 inline-block h-2 w-2 rounded-full bg-critical" />}</div>
          <select className="input !w-auto !py-1 text-xs" value={mode} disabled={active} onChange={(e) => setMode(e.target.value as Mode)}>
            <option value="elevenlabs" disabled={!hasAgent}>ElevenLabs voice agent{hasAgent ? "" : " (set agent ID)"}</option>
            <option value="builtin">Built-in agent (type / browser speech)</option>
            <option value="watch">Watch incoming phone calls</option>
          </select>
        </div>
        <div className="mt-3 flex items-center gap-2">
          {mode !== "watch" && (!active
            ? <button className="btn-primary flex-1 !py-3 text-base" onClick={start} disabled={cfg?.offline}>● Start call</button>
            : <button className="btn-danger flex-1 !py-3 text-base" onClick={hangUp}>■ End call</button>)}
          {mode === "watch" && <div className="flex-1 rounded-lg border border-dashed border-surface-line px-3 py-3 text-center text-sm text-ink-soft">{active ? "Following the live phone call" : "Waiting for a phone call to the TrialReady number…"}</div>}
          {mode === "builtin" && <button className="btn-ghost !py-3" onClick={() => { setVoiceOn((v) => !v); stopSpeaking(); }} title="Read the agent's lines aloud and listen with the browser's speech recognition">{voiceOn ? "🔊 Voice on" : "🔇 Voice off"}</button>}
        </div>
        <div className="mt-2 flex flex-wrap gap-1.5 text-xs">
          {topic && <span className="chip border-brand text-ink">Asking: {topic.replace(/_/g, " ")}</span>}
          {consent.screening && <span className="chip border-good/70">✓ Consent: screening</span>}
          {consent.future_contact && <span className="chip border-good/70">✓ Consent: future contact</span>}
          {consent.future_contact === false && consent.screening && <span className="chip">No future contact</span>}
          {mode === "elevenlabs" && active && <span className="chip">ElevenLabs: {elStatus}</span>}
          {listening && <span className="chip border-critical text-ink"><span className="live-dot h-2 w-2 rounded-full bg-critical" /> listening…</span>}
          {!connected && <span className="chip border-warn/60 text-warn">⚠ event stream offline</span>}
        </div>
        {safety && <div className="mt-3 rounded-lg border border-critical bg-critical/15 p-3 text-sm font-semibold text-ink">⚠ Safety stop — {safety}</div>}
        <Transcript lines={lines} className="mt-3 flex-1" />
        {mode === "builtin" && (
          <form className="mt-3 flex gap-2" onSubmit={(e) => { e.preventDefault(); send(text); }}>
            <input className="input" placeholder={active ? "Type the caller's answer…" : "Start a call first"} value={text} disabled={!active || busy} onChange={(e) => setText(e.target.value)} autoFocus />
            {canListen && <button type="button" className="btn-ghost" disabled={!active || busy} onClick={() => { stopSpeaking(); listenOnce((t) => send(t), setListening); }} title="Speak the answer">🎤</button>}
            <button className="btn-primary" disabled={!active || busy || !text.trim()}>Send</button>
          </form>
        )}
        {mode === "elevenlabs" && hasAgent && <ElevenLabsCall agentId={cfg.elevenlabs_intake_agent_id} sessionId={sessionId} active={active} onLine={add} onStatus={setElStatus} />}
      </section>

      {/* centre: eligibility meter */}
      <section className="col-span-12 min-h-0 max-lg:h-[70vh] lg:col-span-4">
        <EligibilityMeter total={total} funnel={funnel} top={top} selected={selected} onSelect={select} />
      </section>

      {/* right: criteria for the selected trial, or the end card */}
      <section className="card col-span-12 flex min-h-0 flex-col overflow-hidden p-4 max-lg:h-[70vh] lg:col-span-4">
        {end ? (
          <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">
            <div className="label">Result</div>
            <div className="mt-1 text-lg font-semibold">{end.outcome === "no_match" ? "No matching study right now" : `${end.trials.length} ${end.trials.length === 1 ? "study" : "studies"} you may be a good fit for`}</div>
            <p className="mt-1 text-sm text-ink-soft">A research nurse confirms eligibility. This is pre-screening, not a decision.</p>
            <div className="mt-3 space-y-3">{end.trials.map((t: any) => <TrialCard key={t.trial_id} t={t} />)}</div>
            <div className="mt-3 text-xs text-ink-mute">Registry: {consent.future_contact ? "joined — will be called if a new study fits" : consent.future_contact === false ? "not joined" : "asking…"}</div>
            <button className="btn-ghost mt-3" onClick={() => setEnd(null)}>Show criteria table</button>
          </div>
        ) : (
          <>
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="label">Criteria for selected study</div>
                <div className="truncate font-semibold" title={detail?.trial.title}>{detail?.trial.lay_title ?? "—"}</div>
                {detail && <div className="text-xs text-ink-mute">{detail.trial.source === "demo" ? "Demo trial (fictional)" : detail.trial.id} · {detail.criteria.length} criteria{detail.trial.status === "NOT_YET_RECRUITING" ? " · opening soon" : ""}</div>}
              </div>
              {jev && <div className="shrink-0 text-right text-[0.7rem] leading-tight text-ink-mute"><div className="text-sm font-semibold tabular-nums text-ink">{jev.decisions} decisions</div>{jev.latency_ms} ms this turn{jev.mock ? " · mock" : ""}</div>}
            </div>
            <div className="mt-2 flex min-h-0 flex-1 flex-col"><CriteriaTable rows={detail?.criteria ?? []} /></div>
          </>
        )}
      </section>
    </div>
  );
}
