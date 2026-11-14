"use client";
import { ConversationProvider, useConversation } from "@elevenlabs/react";
import { useEffect, useRef } from "react";
import type { Line } from "./Transcript";

/** Real voice path: ElevenLabs Agents web conversation. The agent's server tools call our FastAPI backend,
 *  which pushes scores to the page over SSE - so this component only starts/stops audio and relays the transcript. */
function Inner({ agentId, sessionId, active, onLine, onStatus }: { agentId: string; sessionId: string | null; active: boolean; onLine: (l: Line) => void; onStatus: (s: string) => void }) {
  const conv = useConversation({
    onMessage: (m: any) => onLine({ role: (m.role ?? m.source) === "user" ? "caller" : "agent", text: m.message }),
    onError: (e: any) => onLine({ role: "system", text: `ElevenLabs error: ${typeof e === "string" ? e : e?.message ?? "unknown"}` }),
  });
  const started = useRef(false);
  useEffect(() => { onStatus(conv.status); }, [conv.status]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (active && sessionId && !started.current) {
      started.current = true;
      navigator.mediaDevices.getUserMedia({ audio: true })
        .then(() => conv.startSession({ agentId, connectionType: "webrtc", dynamicVariables: { session_id: sessionId } } as any))
        .catch(() => onLine({ role: "system", text: "Microphone permission is needed for the voice call." }));
    }
    if (!active && started.current) {
      started.current = false;
      conv.endSession();
    }
  }, [active, sessionId]); // eslint-disable-line react-hooks/exhaustive-deps
  return null;
}

export default function ElevenLabsCall(props: { agentId: string; sessionId: string | null; active: boolean; onLine: (l: Line) => void; onStatus: (s: string) => void }) {
  return <ConversationProvider><Inner {...props} /></ConversationProvider>;
}
