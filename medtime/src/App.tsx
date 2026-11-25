/**
 * MedTime – single-file React + TypeScript frontend for the MedTime FastAPI backend.
 * Put this file at src/App.tsx of a Vite "react-ts" project and set VITE_API_URL (see README steps in chat).
 */
import { useCallback, useEffect, useState } from "react";
import type { CSSProperties, FormEvent, ReactNode } from "react";

/* ───────────────────────── API layer ───────────────────────── */
const API_ROOT = "/api";
const BASE = `${API_ROOT}/v1`;
const TOKEN_KEY = "medtime_token";
const store = {
  get: (): string | null => { try { return localStorage.getItem(TOKEN_KEY); } catch { return null; } },
  set: (v: string | null) => { try { v ? localStorage.setItem(TOKEN_KEY, v) : localStorage.removeItem(TOKEN_KEY); } catch { /* ignore */ } },
};
let onAuthFail: () => void = () => {};
const uuid = () => (globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`);

class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) { super(message); this.status = status; }
}

async function api<T = unknown>(path: string, opts: { method?: string; body?: unknown; headers?: Record<string, string> } = {}): Promise<T> {
  const token = store.get();
  let res: Response;
  try {
    res = await fetch(BASE + path, {
      method: opts.method ?? (opts.body !== undefined ? "POST" : "GET"),
      headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}), ...opts.headers },
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
  } catch {
    throw new ApiError("Cannot reach the server. Check your connection or the API address.", 0);
  }
  if (res.status === 204) return undefined as T;
  let data: any = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) {
    if (res.status === 401 && token) onAuthFail();
    const d = data?.detail;
    const msg = typeof d === "string" ? d : Array.isArray(d) ? d.map((x: any) => String(x.msg).replace("Value error, ", "")).join(", ") : "Something went wrong";
    throw new ApiError(msg, res.status);
  }
  return data as T;
}
const errMsg = (e: unknown) => (e instanceof Error ? e.message : "Something went wrong");

/* ───────────────────────── Types ───────────────────────── */
type Status = "scheduled" | "due" | "taken" | "skipped" | "missed" | "no_stock" | "cancelled";
interface Dose { id: string; medicine_id: string; medicine_name: string; strength: string; dose: string; member_id: string; due_at: string; status: Status; is_late: boolean; note: string | null }
interface Adherence { taken: number; skipped: number; missed: number; no_stock: number; eligible: number; adherence_percent: number | null; disclaimer: string }
interface MemberCard { id: string; name: string; relationship: string; is_dependent: boolean; has_login: boolean; counts: Record<string, number>; doses: Dose[]; adherence_7d: Adherence }
interface StockAlert { medicine_id: string; medicine_name: string; quantity: number; member_id: string }
interface Dashboard { date: string; timezone: string; totals: Record<string, number>; members: MemberCard[]; stock_alerts: StockAlert[] }
interface Family { id: string; name: string; timezone: string; role: "admin" | "member" }
interface Med { id: string; member_id: string; display_name: string; strength: string; dose: string; status: "active" | "paused" | "discontinued"; low_stock_threshold: number; stock: number | null }
type DoseAction = "taken" | "skipped" | "no_stock";
type Tab = "today" | "meds" | "stock" | "hist";
type Toaster = (m: string) => void;

const LABEL: Record<string, string> = { taken: "Taken", skipped: "Skipped", missed: "Missed", no_stock: "No medicine left", due: "Due now", scheduled: "Upcoming", cancelled: "Cancelled" };
const fmtTime = (iso: string, tz: string) => new Intl.DateTimeFormat([], { hour: "numeric", minute: "2-digit", timeZone: tz }).format(new Date(iso));
const fmtDay = (iso: string, tz: string) => new Intl.DateTimeFormat([], { day: "numeric", month: "short", timeZone: tz }).format(new Date(iso));
const ymd = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const DEFAULT_TIMES = ["09:00", "14:00", "19:00", "22:00"];

/* ───────────────────────── Small UI pieces ───────────────────────── */
function Chip({ s, late }: { s: string; late?: boolean }) {
  return <span className={`chip ${s}`}>{LABEL[s] ?? s}{late ? " · late" : ""}</span>;
}
function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div className="ov" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="md" role="dialog" aria-modal="true" aria-label={title}>
        <h3>{title}</h3>
        {children}
      </div>
    </div>
  );
}
function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="fld"><span>{label}</span>{children}</label>;
}

/* ───────────────────────── Auth ───────────────────────── */
function Auth({ onDone }: { onDone: (token: string) => void }) {
  const [step, setStep] = useState<"id" | "code">("id");
  const [ident, setIdent] = useState("");
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [hint, setHint] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function send(e: FormEvent) {
    e.preventDefault(); setBusy(true); setErr("");
    try {
      const r = await api<{ dev_code?: string }>("/auth/otp/request", { body: { identifier: ident } });
      if (r.dev_code) { setHint(r.dev_code); setCode(r.dev_code); }
      setStep("code");
    } catch (x) { setErr(errMsg(x)); } finally { setBusy(false); }
  }
  async function verify(e: FormEvent) {
    e.preventDefault(); setBusy(true); setErr("");
    try {
      const r = await api<{ access_token: string }>("/auth/otp/verify", { body: { identifier: ident, code, name: name.trim() || undefined } });
      onDone(r.access_token);
    } catch (x) { setErr(errMsg(x)); } finally { setBusy(false); }
  }
  return (
    <div className="hero">
      <div>
        <span className="chip due">Family health &amp; medicine manager</span>
        <h1>A simpler way for families to care together.</h1>
        <p className="mut">Organise medicines, reminders, dose confirmations and stock for everyone you look after – in one shared space.</p>
        <p className="sm mut">✔ One-tap Taken / Skipped &nbsp; ✔ Missed-dose visibility &nbsp; ✔ Low-stock alerts</p>
      </div>
      <div className="card">
        <h2>{step === "id" ? "Sign in" : "Enter your code"}</h2>
        {step === "id" ? (
          <form onSubmit={send}>
            <Field label="Mobile number or email"><input value={ident} onChange={(e) => setIdent(e.target.value)} required minLength={5} placeholder="+91 98765 43210" autoComplete="username" /></Field>
            <button className="b p" disabled={busy}>{busy ? "Sending…" : "Send code"}</button>
          </form>
        ) : (
          <form onSubmit={verify}>
            <Field label="6-digit code"><input value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} required minLength={6} maxLength={6} inputMode="numeric" autoComplete="one-time-code" /></Field>
            <Field label="Your name (for new accounts)"><input value={name} onChange={(e) => setName(e.target.value)} maxLength={80} placeholder="e.g. Priya" /></Field>
            {hint && <p className="sm mut">Dev mode: code {hint} was filled in for you.</p>}
            <div className="row"><button className="b p" disabled={busy}>{busy ? "Checking…" : "Verify & continue"}</button><button type="button" className="b" onClick={() => setStep("id")}>Back</button></div>
          </form>
        )}
        {err && <p className="err" role="alert">{err}</p>}
      </div>
    </div>
  );
}

/* ───────────────────────── Family setup ───────────────────────── */
function FamilySetup({ onDone, toast, onLogout }: { onDone: () => Promise<void>; toast: Toaster; onLogout: () => void }) {
  const [name, setName] = useState("");
  const [tz, setTz] = useState(Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Kolkata");
  const [invite, setInvite] = useState(new URLSearchParams(window.location.search).get("invite") ?? "");
  const [err, setErr] = useState("");
  async function create(e: FormEvent) {
    e.preventDefault(); setErr("");
    try { await api("/families", { body: { name, timezone: tz } }); await onDone(); toast("Family created – you’re the Admin"); } catch (x) { setErr(errMsg(x)); }
  }
  async function join(e: FormEvent) {
    e.preventDefault(); setErr("");
    try { await api(`/invitations/${encodeURIComponent(invite.trim())}/accept`, { method: "POST" }); await onDone(); toast("You joined the family"); } catch (x) { setErr(errMsg(x)); }
  }
  return (
    <div className="grid">
      <form className="card" onSubmit={create}>
        <h2>Create your family</h2>
        <Field label="Family name"><input value={name} onChange={(e) => setName(e.target.value)} required maxLength={80} placeholder="e.g. Sharma Family" /></Field>
        <Field label="Timezone"><input value={tz} onChange={(e) => setTz(e.target.value)} required /></Field>
        <button className="b p">Create family</button>
      </form>
      <form className="card" onSubmit={join}>
        <h2>Join with an invite</h2>
        <p className="mut sm">Use the invite code your family admin shared. It must match the mobile number or email you signed in with.</p>
        <Field label="Invite code"><input value={invite} onChange={(e) => setInvite(e.target.value)} required /></Field>
        <div className="row"><button className="b p">Join family</button><button type="button" className="b" onClick={onLogout}>Sign out</button></div>
      </form>
      {err && <p className="err" role="alert">{err}</p>}
    </div>
  );
}

/* ───────────────────────── Main shell ───────────────────────── */
function Main({ families, famId, setFamId, onLogout, toast }: { families: Family[]; famId: string; setFamId: (id: string) => void; onLogout: () => void; toast: Toaster }) {
  const fam = families.find((f) => f.id === famId) ?? families[0];
  const [tab, setTab] = useState<Tab>("today");
  const [dash, setDash] = useState<Dashboard | null>(null);
  const [err, setErr] = useState("");
  const [addMember, setAddMember] = useState(false);

  const load = useCallback(async () => {
    try { setDash(await api<Dashboard>(`/families/${fam.id}/dashboard`)); setErr(""); } catch (x) { setErr(errMsg(x)); }
  }, [fam.id]);
  useEffect(() => {
    setDash(null); load();
    const t = window.setInterval(load, 60000);
    return () => window.clearInterval(t);
  }, [load]);

  const tabs: [Tab, string][] = [["today", "Today"], ["meds", "Medicines"], ["stock", "Stock"], ["hist", "History & privacy"]];
  return (
    <>
      <nav aria-label="Sections">
        {tabs.map(([k, l]) => <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{l}</button>)}
      </nav>
      {err && <p className="err" role="alert">{err} <button className="b s" onClick={load}>Retry</button></p>}
      {!dash && !err && <p className="mut">Loading…</p>}
      {dash && tab === "today" && <Today dash={dash} fam={fam} reload={load} toast={toast} onAddMember={() => setAddMember(true)} />}
      {dash && (tab === "meds" || tab === "stock") && <MedsPanel mode={tab} dash={dash} fam={fam} toast={toast} reloadDash={load} />}
      {dash && tab === "hist" && <History dash={dash} fam={fam} toast={toast} onLogout={onLogout} />}
      {addMember && <AddMember famId={fam.id} onClose={() => setAddMember(false)} onDone={() => { setAddMember(false); load(); }} toast={toast} />}
      <FamilyBar families={families} famId={fam.id} setFamId={setFamId} onLogout={onLogout} />
    </>
  );
}
function FamilyBar({ families, famId, setFamId, onLogout }: { families: Family[]; famId: string; setFamId: (id: string) => void; onLogout: () => void }) {
  return (
    <div className="bar2">
      {families.length > 1 ? (
        <select aria-label="Switch family" value={famId} onChange={(e) => setFamId(e.target.value)}>{families.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}</select>
      ) : <b>{families[0].name}</b>}
      <span className="chip scheduled">{families.find((f) => f.id === famId)?.role === "admin" ? "Admin" : "Member"}</span>
      <span className="sp" />
      <button className="b s" onClick={onLogout}>Sign out</button>
    </div>
  );
}

/* ───────────────────────── Today ───────────────────────── */
function Today({ dash, fam, reload, toast, onAddMember }: { dash: Dashboard; fam: Family; reload: () => Promise<void>; toast: Toaster; onAddMember: () => void }) {
  const t = dash.totals;
  const total = Object.values(t).reduce((a, b) => a + b, 0);
  const logged = (t.taken ?? 0) + (t.skipped ?? 0) + (t.no_stock ?? 0);
  const pct = total ? Math.round((logged / total) * 100) : 0;
  const hour = new Date().getHours();

  async function act(id: string, action: DoseAction) {
    try {
      await api(`/doses/${id}/actions`, { body: { action }, headers: { "Idempotency-Key": uuid() } });
      toast(`${LABEL[action]} recorded`);
      await reload();
    } catch (x) { toast(errMsg(x)); }
  }
  return (
    <>
      <div className="card rowb mb">
        <div><h2>Good {hour < 12 ? "morning" : hour < 17 ? "afternoon" : "evening"}</h2><div className="mut">{logged} of {total} doses logged today</div></div>
        <div className="ring" style={{ "--p": pct } as CSSProperties}><b>{pct}%</b></div>
      </div>
      {(t.missed ?? 0) > 0 && <div className="alert missed">⚠ {t.missed} missed dose{t.missed > 1 ? "s" : ""} today</div>}
      {dash.stock_alerts.map((a) => <div key={a.medicine_id} className={`alert ${a.quantity ? "skipped" : "missed"}`}>{a.quantity ? "Low stock" : "Out of stock"}: {a.medicine_name} ({a.quantity} left) – time to refill</div>)}
      <div className="rowb mt"><h3>Family members</h3>{fam.role === "admin" && <button className="b s p" onClick={onAddMember}>+ Add member</button>}</div>
      <div className="grid">
        {dash.members.map((m) => (
          <div className="card" key={m.id}>
            <div className="row"><div className="av">{m.name[0]?.toUpperCase()}</div><div><b>{m.name}</b><div className="sm mut">{m.relationship}{m.adherence_7d.adherence_percent != null && ` · 7-day adherence ${m.adherence_7d.adherence_percent}%`}</div></div></div>
            {m.doses.length === 0 && <p className="mut sm">No medicines scheduled today.</p>}
            {m.doses.map((d) => {
              const open = d.status === "scheduled" || d.status === "due" || d.status === "missed";
              return (
                <div className="dose" key={d.id}>
                  <div><b>{d.medicine_name} {d.strength}</b><div className="sm mut">{d.dose} · {fmtTime(d.due_at, dash.timezone)}</div></div>
                  <div className="row">
                    <Chip s={d.status} late={d.is_late} />
                    {open && <>
                      <button className="b s ok" onClick={() => act(d.id, "taken")}>Taken</button>
                      <button className="b s wa" onClick={() => act(d.id, "skipped")}>Skip</button>
                      <button className="b s er" onClick={() => act(d.id, "no_stock")}>No stock</button>
                    </>}
                  </div>
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </>
  );
}

function AddMember({ famId, onClose, onDone, toast }: { famId: string; onClose: () => void; onDone: () => void; toast: Toaster }) {
  const [name, setName] = useState("");
  const [rel, setRel] = useState("Child");
  const [adult, setAdult] = useState(false);
  const [ident, setIdent] = useState("");
  const [err, setErr] = useState("");
  const [token, setToken] = useState("");
  async function submit(e: FormEvent) {
    e.preventDefault(); setErr("");
    try {
      const r = await api<{ invite_token?: string }>(`/families/${famId}/members`, { body: { name, relationship: rel, is_dependent: !adult, invite_identifier: adult ? ident : undefined } });
      if (r.invite_token) setToken(r.invite_token); else { toast("Member added"); onDone(); }
    } catch (x) { setErr(errMsg(x)); }
  }
  if (token) {
    const link = `${window.location.origin}/?invite=${token}`;
    return (
      <Modal title="Invite created" onClose={onDone}>
        <p>Share this link with {name}. It works only for the number or email you entered and expires in 7 days.</p>
        <input readOnly value={link} onFocus={(e) => e.target.select()} />
        <div className="row mt"><button className="b p" onClick={() => { navigator.clipboard?.writeText(link).then(() => toast("Link copied"), () => toast("Copy the link manually")); }}>Copy link</button><button className="b" onClick={onDone}>Done</button></div>
      </Modal>
    );
  }
  return (
    <Modal title="Add family member" onClose={onClose}>
      <form onSubmit={submit}>
        <Field label="Name"><input value={name} onChange={(e) => setName(e.target.value)} required maxLength={80} /></Field>
        <Field label="Relationship"><select value={rel} onChange={(e) => setRel(e.target.value)}>{["Child", "Mother", "Father", "Spouse", "Other"].map((r) => <option key={r}>{r}</option>)}</select></Field>
        <label className="chk"><input type="checkbox" checked={adult} onChange={(e) => setAdult(e.target.checked)} /> Adult with their own login (send an invite)</label>
        {adult && <Field label="Their mobile number or email"><input value={ident} onChange={(e) => setIdent(e.target.value)} required minLength={5} /></Field>}
        {!adult && <p className="sm mut">Dependents don’t need a login – you’ll log their doses.</p>}
        {err && <p className="err" role="alert">{err}</p>}
        <div className="row mt"><button className="b p">{adult ? "Create invite" : "Add member"}</button><button type="button" className="b" onClick={onClose}>Cancel</button></div>
      </form>
    </Modal>
  );
}

/* ───────────────────────── Medicines & stock ───────────────────────── */
function MedsPanel({ mode, dash, fam, toast, reloadDash }: { mode: "meds" | "stock"; dash: Dashboard; fam: Family; toast: Toaster; reloadDash: () => Promise<void> }) {
  const [meds, setMeds] = useState<Med[] | null>(null);
  const [adding, setAdding] = useState(false);
  const ids = dash.members.map((m) => m.id).join(",");
  const load = useCallback(async () => {
    try {
      const lists = await Promise.all(ids.split(",").filter(Boolean).map((id) => api<Med[]>(`/members/${id}/medicines`)));
      setMeds(lists.flat());
    } catch (x) { toast(errMsg(x)); }
  }, [ids, toast]);
  useEffect(() => { load(); }, [load]);

  async function setStatus(m: Med, status: Med["status"]) {
    if (status === "discontinued" && !window.confirm("Discontinue this medicine? Future reminders will be cancelled.")) return;
    try { await api(`/medicines/${m.id}`, { method: "PATCH", body: { status } }); await load(); await reloadDash(); } catch (x) { toast(errMsg(x)); }
  }
  async function adjust(m: Med, delta: number) {
    try { await api(`/medicines/${m.id}/stock-adjustments`, { body: { delta, reason: "manual_adjustment" } }); await load(); await reloadDash(); } catch (x) { toast(errMsg(x)); }
  }
  if (!meds) return <p className="mut">Loading…</p>;

  return (
    <>
      <div className="rowb mb"><h2>{mode === "meds" ? "Medicines & schedules" : "Medicine stock"}</h2>{mode === "meds" && <button className="b p" onClick={() => setAdding(true)}>+ Add medicine</button>}</div>
      {mode === "stock" && <p className="mut sm">Stock is adjusted manually – it isn’t reduced automatically when doses are logged.</p>}
      <div className="grid">
        {dash.members.map((mem) => {
          const list = meds.filter((m) => m.member_id === mem.id && (mode === "meds" || m.status !== "discontinued"));
          return (
            <div className="card" key={mem.id}>
              <h3>{mem.name}</h3>
              {list.length === 0 && <p className="mut sm">{mode === "meds" ? "No medicines yet." : "Nothing to track."}</p>}
              {list.map((m) => mode === "meds" ? (
                <div className="dose" key={m.id}>
                  <div><b>{m.display_name} {m.strength}</b> {m.status !== "active" && <Chip s="scheduled" late={false} />}<div className="sm mut">{m.dose}{m.status !== "active" ? ` · ${m.status}` : ""}</div></div>
                  <div className="row">
                    {m.status === "active" && <button className="b s" onClick={() => setStatus(m, "paused")}>Pause</button>}
                    {m.status === "paused" && <button className="b s" onClick={() => setStatus(m, "active")}>Resume</button>}
                    {m.status !== "discontinued" && <button className="b s er" onClick={() => setStatus(m, "discontinued")}>Discontinue</button>}
                  </div>
                </div>
              ) : (
                <div className="dose col" key={m.id}>
                  <div className="rowb"><b>{m.display_name} {m.strength}</b>{m.stock != null && m.stock <= m.low_stock_threshold && <span className={`chip ${m.stock ? "skipped" : "missed"}`}>{m.stock ? "Low" : "Empty"}</span>}</div>
                  <div className="bar"><i style={{ width: `${m.stock == null ? 0 : Math.min(100, (m.stock / Math.max(m.low_stock_threshold * 3, 1)) * 100)}%` }} /></div>
                  <div className="rowb"><span>{m.stock == null ? "Not tracked yet" : `${m.stock} units · alert at ${m.low_stock_threshold}`}</span>
                    <div className="row">
                      <button className="b s" disabled={!m.stock} onClick={() => adjust(m, -1)}>−1</button>
                      <button className="b s" onClick={() => adjust(m, 1)}>+1</button>
                      <button className="b s p" onClick={() => adjust(m, 10)}>{m.stock == null ? "Start (+10)" : "+10"}</button>
                    </div></div>
                </div>
              ))}
            </div>
          );
        })}
      </div>
      {adding && <AddMedicine members={dash.members} tz={dash.timezone} onClose={() => setAdding(false)} onDone={async () => { setAdding(false); await load(); await reloadDash(); toast("Medicine saved – reminders scheduled"); }} />}
      {fam.role !== "admin" && mode === "meds" && <p className="sm mut mt">You can manage your own medicines. Admins manage dependents.</p>}
    </>
  );
}

function AddMedicine({ members, tz, onClose, onDone }: { members: MemberCard[]; tz: string; onClose: () => void; onDone: () => Promise<void> }) {
  const [member, setMember] = useState(members[0]?.id ?? "");
  const [name, setName] = useState("");
  const [strength, setStrength] = useState("");
  const [dose, setDose] = useState("1 tablet");
  const [count, setCount] = useState(1);
  const [times, setTimes] = useState<string[]>(DEFAULT_TIMES);
  const [stock, setStock] = useState("");
  const [low, setLow] = useState("5");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault(); setErr(""); setBusy(true);
    let medId = "";
    try {
      const med = await api<{ id: string }>(`/members/${member}/medicines`, { body: { display_name: name, strength, dose, low_stock_threshold: Number(low) || 0, initial_stock: stock === "" ? undefined : Number(stock) } });
      medId = med.id;
      const today = new Intl.DateTimeFormat("en-CA", { timeZone: tz }).format(new Date());
      await api(`/medicines/${med.id}/schedules`, { body: { times: times.slice(0, count), start_date: today } });
      await onDone();
    } catch (x) {
      setErr(medId ? `Medicine saved, but the schedule failed: ${errMsg(x)}. Remove it and try again.` : errMsg(x));
    } finally { setBusy(false); }
  }
  return (
    <Modal title="Add medicine" onClose={onClose}>
      <form onSubmit={submit}>
        <Field label="For"><select value={member} onChange={(e) => setMember(e.target.value)}>{members.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}</select></Field>
        <Field label="Medicine name"><input value={name} onChange={(e) => setName(e.target.value)} required maxLength={120} /></Field>
        <div className="row"><Field label="Strength"><input value={strength} onChange={(e) => setStrength(e.target.value)} placeholder="500 mg" /></Field><Field label="Dose"><input value={dose} onChange={(e) => setDose(e.target.value)} required /></Field></div>
        <Field label="Times per day"><select value={count} onChange={(e) => setCount(Number(e.target.value))}>{[1, 2, 3, 4].map((n) => <option key={n}>{n}</option>)}</select></Field>
        <div className="row">{Array.from({ length: count }, (_, i) => <input key={i} type="time" required aria-label={`Dose time ${i + 1}`} value={times[i]} onChange={(e) => setTimes(times.map((t, j) => (j === i ? e.target.value : t)))} />)}</div>
        <div className="row"><Field label="Stock (optional)"><input type="number" min={0} value={stock} onChange={(e) => setStock(e.target.value)} /></Field><Field label="Alert at"><input type="number" min={0} value={low} onChange={(e) => setLow(e.target.value)} /></Field></div>
        <p className="sm mut">Enter the schedule exactly as prescribed. MedTime never suggests or changes doses.</p>
        {err && <p className="err" role="alert">{err}</p>}
        <div className="row"><button className="b p" disabled={busy}>{busy ? "Saving…" : "Save medicine"}</button><button type="button" className="b" onClick={onClose}>Cancel</button></div>
      </form>
    </Modal>
  );
}

/* ───────────────────────── History & privacy ───────────────────────── */
function History({ dash, fam, toast, onLogout }: { dash: Dashboard; fam: Family; toast: Toaster; onLogout: () => void }) {
  const [member, setMember] = useState(dash.members[0]?.id ?? "");
  const [from, setFrom] = useState(ymd(new Date(Date.now() - 6 * 864e5)));
  const [to, setTo] = useState(ymd(new Date()));
  const [adh, setAdh] = useState<Adherence | null>(null);
  const [rows, setRows] = useState<Dose[]>([]);

  useEffect(() => {
    if (!member) return;
    let alive = true;
    const q = `from=${from}&to=${to}`;
    Promise.all([api<Adherence>(`/members/${member}/adherence?${q}`), api<{ items: Dose[] }>(`/members/${member}/doses?${q}&limit=200`)])
      .then(([a, d]) => { if (alive) { setAdh(a); setRows(d.items.filter((x) => x.status !== "scheduled").reverse()); } })
      .catch((x) => toast(errMsg(x)));
    return () => { alive = false; };
  }, [member, from, to, toast]);

  async function exportData() {
    try {
      const data = await api("/me/export");
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
      const a = Object.assign(document.createElement("a"), { href: url, download: "medtime-my-data.json" });
      a.click(); URL.revokeObjectURL(url);
    } catch (x) { toast(errMsg(x)); }
  }
  async function deleteAccount() {
    if (!window.confirm("Delete your account? This signs you out and removes your personal details. It cannot be undone.")) return;
    try { await api("/me", { method: "DELETE" }); onLogout(); } catch (x) { toast(errMsg(x)); }
  }

  return (
    <>
      <h2>History & reports</h2>
      <div className="card mb">
        <div className="row">
          <Field label="Member"><select value={member} onChange={(e) => setMember(e.target.value)}>{dash.members.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}</select></Field>
          <Field label="From"><input type="date" value={from} max={to} onChange={(e) => setFrom(e.target.value)} /></Field>
          <Field label="To"><input type="date" value={to} min={from} onChange={(e) => setTo(e.target.value)} /></Field>
        </div>
        {adh && (
          <>
            <div className="big">{adh.adherence_percent == null ? "–" : `${adh.adherence_percent}%`}</div>
            <div className="sm mut">{adh.taken} taken of {adh.eligible} eligible doses · {adh.skipped} skipped · {adh.missed} missed · {adh.no_stock} no stock</div>
            <div className="sm mut">{adh.disclaimer}</div>
          </>
        )}
      </div>
      <div className="card mb tbl">
        <table>
          <thead><tr><th>Date</th><th>Time</th><th>Medicine</th><th>Status</th></tr></thead>
          <tbody>
            {rows.map((d) => <tr key={d.id}><td>{fmtDay(d.due_at, dash.timezone)}</td><td>{fmtTime(d.due_at, dash.timezone)}</td><td>{d.medicine_name} {d.strength}</td><td><Chip s={d.status} late={d.is_late} /></td></tr>)}
            {rows.length === 0 && <tr><td colSpan={4} className="mut">No logged doses in this range.</td></tr>}
          </tbody>
        </table>
      </div>
      <div className="card">
        <h3>Your data</h3>
        <p className="sm mut">Signed in to {fam.name} as {fam.role}. You can download your data or delete your account at any time.</p>
        <div className="row"><button className="b" onClick={exportData}>Export my data</button><button className="b er" onClick={deleteAccount}>Delete my account</button></div>
      </div>
    </>
  );
}

/* ───────────────────────── App root ───────────────────────── */
export default function App() {
  const [token, setToken] = useState<string | null>(store.get());
  const [families, setFamilies] = useState<Family[]>([]);
  const [famId, setFamId] = useState("");
  const [ready, setReady] = useState(false);
  const [toastMsg, setToastMsg] = useState("");

  const toast = useCallback<Toaster>((m) => { setToastMsg(m); window.setTimeout(() => setToastMsg(""), 2800); }, []);
  const logout = useCallback(() => { store.set(null); setToken(null); setFamilies([]); setFamId(""); setReady(true); }, []);
  useEffect(() => { onAuthFail = logout; }, [logout]);

  const loadFamilies = useCallback(async () => {
    const f = await api<Family[]>("/families");
    setFamilies(f);
    setFamId((prev) => f.find((x) => x.id === prev)?.id ?? f[0]?.id ?? "");
  }, []);
  useEffect(() => {
    if (!token) { setReady(true); return; }
    let alive = true;
    loadFamilies().catch(() => undefined).finally(() => alive && setReady(true));
    return () => { alive = false; };
  }, [token, loadFamilies]);

  let body: ReactNode;
  if (!ready) body = <p className="mut">Loading…</p>;
  else if (!token) body = <Auth onDone={(t) => { store.set(t); setReady(false); setToken(t); }} />;
  else if (families.length === 0) body = <FamilySetup onDone={loadFamilies} toast={toast} onLogout={logout} />;
  else body = <Main families={families} famId={famId} setFamId={setFamId} onLogout={logout} toast={toast} />;

  return (
    <>
      <style>{CSS}</style>
      <header className="top"><div className="logo"><i>✚</i>MedTime</div></header>
      <main className="wrap">{body}</main>
      <footer>MedTime records self-reported dose status. It is not a substitute for professional medical advice and never suggests changing a prescription.</footer>
      {toastMsg && <div className="toast" role="status">{toastMsg}</div>}
    </>
  );
}

/* ───────────────────────── Styles (white + orange) ───────────────────────── */
const CSS = `
:root{--bg:#fff;--card:#fff;--tx:#1f2430;--mut:#6b7280;--pri:#d9480f;--ln:#ececec;--ok:#1b8a4b;--okb:#dff3e7;--wa:#a86500;--wab:#fdf0d5;--er:#b3261e;--erb:#fbe3e1;--in:#1f5fbf;--inb:#e0ecfb;--grb:#f3f3f3;--sh:0 2px 10px rgba(31,36,48,.08);color-scheme:light}
*{box-sizing:border-box}html{background:var(--bg)}
body{margin:0;background:var(--bg);color:var(--tx);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
button,input,select{font:inherit;color:inherit}
.top{position:sticky;top:0;z-index:20;background:var(--card);border-bottom:1px solid var(--ln);padding:10px 16px;padding-top:calc(10px + env(safe-area-inset-top,0px))}
.logo{display:flex;align-items:center;gap:8px;font-weight:700;font-size:18px;max-width:980px;margin:0 auto}
.logo i{width:30px;height:30px;border-radius:9px;background:var(--pri);color:#fff;display:grid;place-items:center;font-style:normal}
.wrap{max-width:980px;margin:0 auto;padding:16px 16px 110px}
nav{display:flex;gap:4px;background:var(--card);border:1px solid var(--ln);border-radius:14px;padding:4px;margin-bottom:16px;overflow-x:auto}
nav button{flex:1;border:0;background:none;padding:9px 12px;border-radius:10px;cursor:pointer;color:var(--mut);font-weight:600;white-space:nowrap}
nav button.on{background:var(--pri);color:#fff}
.card{background:var(--card);border:1px solid var(--ln);border-radius:16px;padding:16px;box-shadow:var(--sh)}
.grid{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(290px,1fr))}
h1,h2,h3{margin:0 0 8px;line-height:1.25}h1{font-size:clamp(28px,6vw,42px)}h2{font-size:20px}h3{font-size:16px}
.mut{color:var(--mut)}.sm{font-size:13px}.mb{margin-bottom:14px}.mt{margin-top:12px}.sp{flex:1}
.b{border:1px solid var(--ln);background:var(--card);border-radius:10px;padding:8px 14px;cursor:pointer;font-weight:600}
.b:hover:not(:disabled){transform:translateY(-1px)}.b:disabled{opacity:.45;cursor:not-allowed}
.b:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid var(--pri);outline-offset:2px}
.b.p{background:var(--pri);border-color:var(--pri);color:#fff}.b.ok{background:var(--okb);color:var(--ok);border-color:transparent}.b.wa{background:var(--wab);color:var(--wa);border-color:transparent}.b.er{background:var(--erb);color:var(--er);border-color:transparent}.b.s{padding:5px 10px;font-size:13px}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.rowb{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap}
.chip{display:inline-block;padding:2px 10px;border-radius:99px;font-size:12px;font-weight:700}
.taken{background:var(--okb);color:var(--ok)}.skipped{background:var(--wab);color:var(--wa)}.missed,.no_stock{background:var(--erb);color:var(--er)}.due{background:var(--inb);color:var(--in)}.scheduled,.cancelled{background:var(--grb);color:var(--mut)}
.hero{display:grid;gap:24px;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));align-items:center;padding:20px 0}
.fld{display:block;flex:1;min-width:120px;margin:8px 0}.fld>span{display:block;font-weight:600;font-size:13px;margin-bottom:4px}
.chk{display:flex;gap:8px;align-items:center;margin:10px 0;font-size:14px}.chk input{width:auto}
input,select{width:100%;padding:10px 12px;border:1px solid var(--ln);border-radius:10px;background:#fff}
.ring{--p:0;width:92px;height:92px;border-radius:50%;background:conic-gradient(var(--pri) calc(var(--p)*1%),var(--grb) 0);display:grid;place-items:center;flex:none}
.ring b{background:var(--card);width:70px;height:70px;border-radius:50%;display:grid;place-items:center;font-size:17px}
.av{width:38px;height:38px;border-radius:50%;background:var(--pri);color:#fff;display:grid;place-items:center;font-weight:700;flex:none}
.dose{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:10px 0;border-top:1px solid var(--ln);flex-wrap:wrap}.dose.col{flex-direction:column;align-items:stretch}
.alert{border-radius:12px;padding:10px 14px;margin-bottom:8px;font-weight:600;font-size:14px}
.bar{height:8px;background:var(--grb);border-radius:9px;overflow:hidden}.bar i{display:block;height:100%;background:var(--pri)}
.big{font-size:32px;font-weight:700;color:var(--pri);margin-top:10px}
.err{color:var(--er);background:var(--erb);border-radius:10px;padding:8px 12px;font-size:14px}
.ov{position:fixed;inset:0;background:rgba(20,20,30,.5);z-index:50;display:grid;place-items:center;padding:16px}
.md{background:#fff;border-radius:18px;padding:20px;width:100%;max-width:440px;max-height:90vh;overflow:auto}
.toast{position:fixed;left:50%;bottom:calc(76px + env(safe-area-inset-bottom,0px));transform:translateX(-50%);background:var(--tx);color:#fff;padding:10px 18px;border-radius:12px;z-index:60;font-weight:600;max-width:90vw}
.bar2{position:fixed;left:0;right:0;bottom:0;z-index:25;background:var(--card);border-top:1px solid var(--ln);display:flex;gap:10px;align-items:center;padding:10px 16px calc(10px + env(safe-area-inset-bottom,0px))}
.bar2 select{width:auto}
.tbl{overflow-x:auto}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:8px;border-top:1px solid var(--ln);font-size:14px}th{border-top:0}
footer{text-align:center;color:var(--mut);font-size:12px;padding:0 16px 90px}
`;
