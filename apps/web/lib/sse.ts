"use client";
import { useEffect, useRef, useState } from "react";
import { API } from "./api";

export type Ev = { type: string; session_id: string | null; ts: number; data: any };
const TYPES = ["session_started", "consent", "next_question", "turn_scored", "safety", "withdrawn", "screening_done", "referral_created",
  "referral_updated", "call_status", "rematch_progress", "trial_added"];

/** Subscribe to the backend event stream. The handler always sees the latest closure. */
export function useEvents(handler: (ev: Ev) => void): boolean {
  const ref = useRef(handler);
  ref.current = handler;
  const [connected, setConnected] = useState(false);
  useEffect(() => {
    const es = new EventSource(`${API}/api/events`);
    const on = (e: MessageEvent) => { try { ref.current(JSON.parse(e.data)); } catch { /* ignore malformed frame */ } };
    TYPES.forEach((t) => es.addEventListener(t, on as EventListener));
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    return () => es.close();
  }, []);
  return connected;
}
