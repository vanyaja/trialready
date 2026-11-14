"use client";
/** Browser speech fallback (Web Speech API) for the built-in call. ElevenLabs is the real voice path. */

export function speak(text: string, onEnd?: () => void) {
  if (typeof window === "undefined" || !("speechSynthesis" in window)) { onEnd?.(); return; }
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = "en-GB";
  u.rate = 1.02;
  const v = window.speechSynthesis.getVoices().find((x) => x.lang === "en-GB");
  if (v) u.voice = v;
  u.onend = () => onEnd?.();
  u.onerror = () => onEnd?.();
  window.speechSynthesis.speak(u);
}

export function stopSpeaking() {
  if (typeof window !== "undefined" && "speechSynthesis" in window) window.speechSynthesis.cancel();
}

export function recognitionAvailable(): boolean {
  return typeof window !== "undefined" && !!((window as any).SpeechRecognition || (window as any).webkitSpeechRecognition);
}

export function listenOnce(onText: (t: string) => void, onState?: (listening: boolean) => void): () => void {
  const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
  if (!SR) return () => {};
  const rec = new SR();
  rec.lang = "en-GB";
  rec.interimResults = false;
  rec.maxAlternatives = 1;
  rec.onresult = (e: any) => onText(e.results[0][0].transcript);
  rec.onstart = () => onState?.(true);
  rec.onend = () => onState?.(false);
  rec.onerror = () => onState?.(false);
  try { rec.start(); } catch { onState?.(false); }
  return () => { try { rec.abort(); } catch { /* already stopped */ } };
}
