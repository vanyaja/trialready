"use client";
import { useEffect, useState } from "react";
import { get, post } from "@/lib/api";

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return <div className="card p-4"><div className="label">{label}</div><div className="mt-1 text-3xl font-bold tabular-nums">{value}</div></div>;
}

export default function Registry() {
  const [r, setR] = useState<any>(null);
  const [edit, setEdit] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("+44");
  const [ok, setOk] = useState(false);
  const [msg, setMsg] = useState("");
  const load = () => get("/api/registry").then(setR).catch(() => setMsg("Cannot reach the API."));
  useEffect(() => { load(); }, []);
  const save = async () => {
    try { await post(`/api/registry/${edit}/contact`, { first_name: name || null, phone_e164: phone.replace(/\s/g, ""), consent_confirmed: ok }); setEdit(null); setName(""); setPhone("+44"); setOk(false); setMsg("Phone number attached."); load(); }
    catch (e: any) { setMsg(e.message); }
  };
  if (!r) return <div className="text-sm text-ink-mute">{msg || "Loading…"}</div>;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        <Stat label="Registry members" value={r.members} /><Stat label="With a phone number" value={r.with_phone} /><Stat label="Sessions" value={r.consent.sessions} />
        <Stat label="Consented to screening" value={r.consent.consent_screening} /><Stat label="Consented to future contact" value={r.consent.consent_contact} />
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="card p-4"><div className="label">Languages</div>{r.languages.map((l: any) => <div key={l.key} className="mt-1.5 flex justify-between text-sm"><span>{l.key}</span><span className="tabular-nums text-ink-soft">{l.n}</span></div>)}</div>
        <div className="card p-4"><div className="label">Channels</div>{r.channels.map((l: any) => <div key={l.key} className="mt-1.5 flex justify-between text-sm"><span>{l.key === "sim" ? "seeded persona" : l.key}</span><span className="tabular-nums text-ink-soft">{l.n}</span></div>)}</div>
        <div className="card p-4 text-sm text-ink-soft"><div className="label">Data handling</div><p className="mt-1.5">{r.synthetic} of {r.members} members are synthetic personas. Withdrawn: {r.consent.withdrawn}. Safety stops: {r.consent.safety_stops}. Phone numbers are stored apart from profiles and are deleted by <code>scripts/purge.py</code> after the event.</p></div>
      </div>
      <div className="card p-4">
        <div className="label">Members</div>
        {msg && <div className="mt-2 text-sm text-ink-soft">{msg}</div>}
        <table className="mt-2 w-full text-left text-sm">
          <thead><tr className="label border-b border-surface-line"><th className="py-1.5">Person</th><th>Age band</th><th>Sex</th><th>District</th><th>Language</th><th>Ethnic group (self-reported)</th><th>Topics on file</th><th>Phone</th></tr></thead>
          <tbody>
            {r.list.map((m: any) => (
              <tr key={m.profile_id} className="border-b border-surface-line/50">
                <td className="py-1.5 font-medium">{m.pseudonym}{m.synthetic && <span className="ml-1.5 text-xs font-normal text-ink-mute">synthetic</span>}</td>
                <td>{m.age_band ?? "–"}</td><td>{m.sex ?? "–"}</td><td>{m.district ?? "–"}</td><td>{m.language}</td><td>{m.ethnicity ?? "not given"}</td><td className="tabular-nums">{m.topics.length}</td>
                <td>{m.has_phone ? "✓ on file" : <button className="chip hover:border-brand" onClick={() => { setEdit(m.profile_id); setMsg(""); }}>+ add</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {edit && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
          <div className="card w-full max-w-md space-y-3 p-5">
            <div className="text-lg font-semibold">Attach a consenting attendee&apos;s phone</div>
            <p className="text-sm text-ink-soft">Only for people in the room who have explicitly agreed to be called. On a Twilio trial account the number must also be verified in Twilio.</p>
            <input className="input" placeholder="First name (optional)" value={name} onChange={(e) => setName(e.target.value)} />
            <input className="input" placeholder="+447700900123" value={phone} onChange={(e) => setPhone(e.target.value)} />
            <label className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1" checked={ok} onChange={(e) => setOk(e.target.checked)} /> This person has agreed, today, to receive a demo call on this number.</label>
            <div className="flex justify-end gap-2"><button className="btn-ghost" onClick={() => setEdit(null)}>Cancel</button><button className="btn-primary" disabled={!ok} onClick={save}>Save</button></div>
          </div>
        </div>
      )}
    </div>
  );
}
