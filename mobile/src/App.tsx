import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from "react";
import * as A from "./api";

const LazyScanner = lazy(() => import("./Scanner"));         // the barcode library loads only when the camera opens
const Scanner = (p: { onCode: (c: string) => void; onClose: () => void }) => (
  <Suspense fallback={<div className="scanner"><div className="scan-bar"><span>Starting camera…</span></div></div>}><LazyScanner {...p} /></Suspense>
);

type Trip = { to_number: string; from_id: string; to_id: string; pieces: number; net_benefit: number; reason: string; region: string;
  decision: string; released: boolean; urgent: number; lines: number };
type Line = { move_id: string; sku_id: string; style_name: string; size: string; qty: number; approved_qty: number; decision: string; reason: string };
type Transfer = { to_number: string; from_location: string; to_location: string; status: string; planned: number; sent: number; received: number;
  ship_date: string; expected: string };
type TLine = { sku_id: string; planned: number; sent: number; received: number };
type Exc = { id: number; severity: string; kind: string; title: string; detail: string; location: string; region: string; first_seen: string };
type Item = { sku_id: string; name: string; barcode: string };
type Tab = "home" | "approve" | "move" | "count" | "issues";

const money = (v: number) => (Math.abs(v) >= 1e7 ? `₹${(v / 1e7).toFixed(2)} Cr` : Math.abs(v) >= 1e5 ? `₹${(v / 1e5).toFixed(1)} L` : `₹${Math.round(v).toLocaleString("en-IN")}`);
const today = () => new Date(Date.now() - 864e5).toISOString().slice(0, 10);

export default function App() {
  const [s, setS] = useState<A.Session | null>(A.loadSession());
  const [me, setMe] = useState<A.Perms | null>(null);
  const [tab, setTab] = useState<Tab>("home");
  const [toast, setToast] = useState("");
  const [online, setOnline] = useState(navigator.onLine);
  const say = useCallback((m: string) => { setToast(m); setTimeout(() => setToast(""), 3500); }, []);

  useEffect(() => {
    if (!s) return;
    A.get<A.Perms>(s, "/me").then(setMe).catch((e) => { if (e.status === 401) setS(null); });
  }, [s]);
  useEffect(() => {
    const up = async () => {
      setOnline(true);
      if (s && A.outbox().length) {
        const r = await A.flush(s);
        say(`Back online: ${r.sent} saved` + (r.failed.length ? `, ${r.failed.length} refused` : ""));
      }
    };
    const down = () => setOnline(false);
    window.addEventListener("online", up);
    window.addEventListener("offline", down);
    return () => { window.removeEventListener("online", up); window.removeEventListener("offline", down); };
  }, [s, say]);

  if (!s) return <Login onIn={setS} />;
  const signOut = () => { A.saveSession(null); setS(null); setMe(null); };
  return (
    <div className="app">
      <header>
        <div><b>StockFlow</b><small>{me ? `${me.user} · ${me.role}${me.stores.length ? " · " + me.stores.join(", ") : ""}` : s.user}</small></div>
        <button className="link" onClick={signOut}>Sign out</button>
      </header>
      {!online && <div className="banner">Offline. Counts and receipts are kept on this phone and sent when you are back online ({A.outbox().length} waiting).</div>}
      <main>
        {tab === "home" && <Home s={s} me={me} go={setTab} />}
        {tab === "approve" && <Approve s={s} me={me} say={say} />}
        {tab === "move" && <Moves s={s} me={me} say={say} />}
        {tab === "count" && <Count s={s} me={me} say={say} />}
        {tab === "issues" && <Issues s={s} me={me} say={say} />}
      </main>
      {toast && <div className="toast">{toast}</div>}
      <nav>
        {([["home", "🏠", "Today"], ["approve", "✅", "Approve"], ["move", "🚚", "Transfers"], ["count", "📦", "Count"], ["issues", "⚠️", "Issues"]] as const).map(([k, i, l]) => (
          <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}><span>{i}</span>{l}</button>
        ))}
      </nav>
    </div>
  );
}

function Login({ onIn }: { onIn: (s: A.Session) => void }) {
  const [server, setServer] = useState(localStorage.getItem("sf.server") || A.defaultServer());
  const [u, setU] = useState("");
  const [p, setP] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const go = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr("");
    try {
      localStorage.setItem("sf.server", server);
      onIn(await A.signIn(server, u.trim(), p));
    } catch (x) {
      setErr(x instanceof A.ApiError ? x.message : "Can't reach the server. Check the address and your connection.");
    } finally { setBusy(false); }
  };
  return (
    <form className="login" onSubmit={go}>
      <div className="logo">📦</div>
      <h1>StockFlow</h1>
      <p>Approve transfers, mark them sent and received, count stock.</p>
      {!A.defaultServer() && <label>Server<input value={server} onChange={(e) => setServer(e.target.value)} placeholder="https://stock.yourcompany.com" inputMode="url" autoCapitalize="none" required /></label>}
      <label>User name<input value={u} onChange={(e) => setU(e.target.value)} autoCapitalize="none" autoComplete="username" required /></label>
      <label>Password<input type="password" value={p} onChange={(e) => setP(e.target.value)} autoComplete="current-password" required /></label>
      {err && <div className="err">{err}</div>}
      <button className="btn" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
    </form>
  );
}

function useLoad<T>(fn: () => Promise<T>, deps: unknown[]): [T | null, string, () => void] {
  const [d, setD] = useState<T | null>(null);
  const [e, setE] = useState("");
  const [n, setN] = useState(0);
  useEffect(() => {
    let live = true;
    setE("");
    fn().then((x) => live && setD(x)).catch((x) => live && setE(x.message || "Could not load"));
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, n]);
  return [d, e, () => setN((k) => k + 1)];
}

function Home({ s, me, go }: { s: A.Session; me: A.Perms | null; go: (t: Tab) => void }) {
  const [p, e] = useLoad(() => A.get<{ plan: { plan_date: string; brand: string; trips: number; pieces: number; net_benefit: number }; trips: Trip[] }>(s, "/plans/latest"), [s]);
  const [tr] = useLoad(() => A.get<Transfer[]>(s, "/transfers"), [s]);
  const [x] = useLoad(() => A.get<Exc[]>(s, "/exceptions"), [s]);
  const mine = (loc: string) => !me?.stores.length || me.stores.includes(loc);
  const pending = p?.trips.filter((t) => t.decision === "pending" && !t.released).length ?? 0;
  const toSend = tr?.filter((t) => ["Waiting to leave", "Late leaving"].includes(t.status) && mine(t.from_location)).length ?? 0;
  const toGet = tr?.filter((t) => ["In transit", "Late arriving"].includes(t.status) && mine(t.to_location)).length ?? 0;
  return (
    <section>
      {e && <div className="err">{e}</div>}
      {p && <div className="hero"><small>{p.plan.brand} · plan for {p.plan.plan_date}</small><h2>{p.plan.trips} trips · {p.plan.pieces.toLocaleString("en-IN")} units</h2><small>net gain {money(p.plan.net_benefit)}</small></div>}
      <div className="tiles">
        {A.can(me, "approve") && <Tile n={pending} l="trips to decide" onClick={() => go("approve")} />}
        <Tile n={toSend} l="to send out" onClick={() => go("move")} />
        <Tile n={toGet} l="to receive" onClick={() => go("move")} />
        <Tile n={x?.length ?? 0} l="open issues" warn={!!x?.some((i) => i.severity === "high")} onClick={() => go("issues")} />
      </div>
      <button className="btn wide" onClick={() => go("count")}>📦 Count stock</button>
    </section>
  );
}

const Tile = ({ n, l, onClick, warn }: { n: number; l: string; onClick: () => void; warn?: boolean }) => (
  <button className={`tile${warn ? " warn" : ""}`} onClick={onClick}><b>{n}</b><span>{l}</span></button>
);

function Approve({ s, me, say }: { s: A.Session; me: A.Perms | null; say: (m: string) => void }) {
  const [p, e, reload] = useLoad(() => A.get<{ plan: { plan_id: string }; trips: Trip[]; regions: { region: string; pending: number }[] }>(s, "/plans/latest"), [s]);
  const [region, setRegion] = useState("");
  const [open, setOpen] = useState<Trip | null>(null);
  if (e) return <div className="err">{e}</div>;
  if (!p) return <Spinner />;
  if (!A.can(me, "approve")) return <div className="empty">Your role can see trips but not decide on them.</div>;
  const pid = p.plan.plan_id;
  if (open) return <TripView s={s} pid={pid} t={open} back={() => { setOpen(null); reload(); }} say={say} />;
  const trips = p.trips.filter((t) => !region || t.region === region).sort((a, b) => Number(a.released) - Number(b.released) || b.urgent - a.urgent);
  const approved = p.trips.filter((t) => t.decision === "approved" && !t.released);
  const release = async () => {
    try {
      const r = await A.post<{ released: string[]; pieces: number }>(s, `/plans/${pid}/release`, { to_numbers: approved.map((t) => t.to_number) }, "release");
      say("queued" in r ? "Offline: release will be sent later" : `Released ${r.released.length} trips (${r.pieces} units)`);
      reload();
    } catch (x) { say((x as Error).message); }
  };
  return (
    <section>
      <div className="chips">
        <button className={!region ? "on" : ""} onClick={() => setRegion("")}>All</button>
        {p.regions.map((r) => <button key={r.region} className={region === r.region ? "on" : ""} onClick={() => setRegion(r.region)}>{r.region} · {r.pending}</button>)}
      </div>
      {trips.map((t) => (
        <button key={t.to_number} className="card row" onClick={() => setOpen(t)}>
          <div><b>{t.from_id} → {t.to_id}</b><small>{t.to_number} · {t.pieces} units · {money(t.net_benefit)}{t.urgent ? " · urgent" : ""}</small><small className="why">{t.reason}</small></div>
          <span className={`pill ${t.released ? "done" : t.decision}`}>{t.released ? "released" : t.decision}</span>
        </button>
      ))}
      {A.can(me, "release") && approved.length > 0 && <button className="btn wide sticky" onClick={release}>📤 Release {approved.length} approved trips</button>}
    </section>
  );
}

function TripView({ s, pid, t, back, say }: { s: A.Session; pid: string; t: Trip; back: () => void; say: (m: string) => void }) {
  const [lines] = useLoad(() => A.get<Line[]>(s, `/plans/${pid}/trips/${t.to_number}`), [t.to_number]);
  const [qty, setQty] = useState<Record<string, number>>({});
  const decide = async (decision: string) => {
    const ch = Object.entries(qty).map(([move_id, q]) => ({ move_id, decision: q === 0 ? "rejected" : "approved", approved_qty: q, note: "phone" }));
    try {
      if (ch.length) await A.post(s, `/plans/${pid}/lines`, ch, "line changes");
      const r = await A.post(s, `/plans/${pid}/trips/${t.to_number}/decision`, { decision, note: "" }, `${decision} ${t.to_number}`);
      say("queued" in (r as object) ? "Offline: saved on the phone" : `${t.to_number} ${decision}`);
      back();
    } catch (x) { say((x as Error).message); }
  };
  return (
    <section>
      <button className="link" onClick={back}>‹ Trips</button>
      <h3>{t.from_id} → {t.to_id}</h3>
      <p className="why">{t.reason}</p>
      {!lines ? <Spinner /> : lines.map((l) => (
        <div key={l.move_id} className="card line">
          <div><b>{l.style_name}</b><small>{l.sku_id}{l.size ? ` · ${l.size}` : ""}</small></div>
          <input type="number" min={0} max={l.qty} inputMode="numeric" disabled={t.released}
            value={qty[l.move_id] ?? l.approved_qty ?? l.qty}
            onChange={(e) => setQty({ ...qty, [l.move_id]: Math.max(0, Math.min(l.qty, Number(e.target.value))) })} />
        </div>
      ))}
      {!t.released && <div className="pair"><button className="btn light" onClick={() => decide("rejected")}>Reject trip</button><button className="btn" onClick={() => decide("approved")}>Approve</button></div>}
    </section>
  );
}

function Moves({ s, me, say }: { s: A.Session; me: A.Perms | null; say: (m: string) => void }) {
  const [list, e, reload] = useLoad(() => A.get<Transfer[]>(s, "/transfers"), [s]);
  const [open, setOpen] = useState<Transfer | null>(null);
  if (e) return <div className="err">{e}</div>;
  if (!list) return <Spinner />;
  if (open) return <MoveView s={s} me={me} t={open} back={() => { setOpen(null); reload(); }} say={say} />;
  const active = list.filter((t) => !t.status.startsWith("Received"));
  return (
    <section>
      {active.length === 0 && <div className="empty">No transfers waiting.</div>}
      {active.map((t) => (
        <button key={t.to_number} className="card row" onClick={() => setOpen(t)}>
          <div><b>{t.from_location} → {t.to_location}</b><small>{t.to_number} · {t.planned} units · due {t.expected}</small></div>
          <span className={`pill ${t.status.startsWith("Late") ? "rejected" : "pending"}`}>{t.status}</span>
        </button>
      ))}
    </section>
  );
}

function MoveView({ s, me, t, back, say }: { s: A.Session; me: A.Perms | null; t: Transfer; back: () => void; say: (m: string) => void }) {
  const [d] = useLoad(() => A.get<{ lines: TLine[] }>(s, `/transfers/${t.to_number}`), [t.to_number]);
  const [n, setN] = useState<Record<string, number>>({});
  const [scan, setScan] = useState(false);
  const [cat] = useLoad(() => A.get<Item[]>(s, "/catalog?limit=500"), [s]);
  const sending = ["Waiting to leave", "Late leaving"].includes(t.status);
  const mine = (loc: string) => A.can(me, "track_all") || (A.can(me, "track_own") && !!me?.stores.includes(loc));
  const allowed = mine(sending ? t.from_location : t.to_location);
  const base = (l: TLine) => (sending ? l.planned : l.sent || l.planned);
  const onCode = useCallback((code: string) => {
    const it = cat?.find((c) => c.barcode === code || c.sku_id === code.toUpperCase());
    if (!it) { say(`${code} is not in the product list`); return; }
    setN((m) => ({ ...m, [it.sku_id]: (m[it.sku_id] ?? 0) + 1 }));
  }, [cat, say]);
  if (!d) return <Spinner />;
  const scanned = Object.keys(n).length > 0;
  const save = async () => {
    const lines = Object.fromEntries(d.lines.map((l) => [l.sku_id, n[l.sku_id] ?? (scanned ? 0 : base(l))]));
    try {
      const r = await A.post(s, `/transfers/${t.to_number}/${sending ? "dispatch" : "receive"}`, { lines, note: scanned ? "scanned on phone" : "phone" }, `${sending ? "sent" : "received"} ${t.to_number}`);
      say("queued" in (r as object) ? "Offline: saved on the phone" : sending ? "Marked as sent" : "Marked as received");
      back();
    } catch (x) { say((x as Error).message); }
  };
  return (
    <section>
      {scan && <Scanner onCode={onCode} onClose={() => setScan(false)} />}
      <button className="link" onClick={back}>‹ Transfers</button>
      <h3>{t.from_location} → {t.to_location}</h3>
      <p className="why">{t.to_number} · {t.status}</p>
      {allowed && <button className="btn light wide" onClick={() => setScan(true)}>📷 Scan items {sending ? "packed" : "received"}</button>}
      {d.lines.map((l) => {
        const got = n[l.sku_id];
        const want = base(l);
        return (
          <div key={l.sku_id} className={`card line${got !== undefined && got !== want ? " diff" : ""}`}>
            <div><b>{cat?.find((c) => c.sku_id === l.sku_id)?.name || l.sku_id}</b><small>{l.sku_id} · expected {want}</small></div>
            <input type="number" min={0} inputMode="numeric" disabled={!allowed} value={got ?? (scanned ? 0 : want)} onChange={(e) => setN({ ...n, [l.sku_id]: Math.max(0, Number(e.target.value)) })} />
          </div>
        );
      })}
      {allowed ? <button className="btn wide sticky" onClick={save}>{sending ? "✔ Mark as sent" : "✔ Mark as received"}</button>
        : <div className="empty">Only {sending ? t.from_location : t.to_location} can record this step.</div>}
    </section>
  );
}

function Count({ s, me, say }: { s: A.Session; me: A.Perms | null; say: (m: string) => void }) {
  const [locs] = useLoad(() => A.get<{ id: string; type: string }[]>(s, "/locations"), [s]);
  const [cat] = useLoad(() => A.get<Item[]>(s, "/catalog?limit=500"), [s]);
  const [loc, setLoc] = useState(me?.stores[0] || "");
  const [day, setDay] = useState(today());
  const [lines, setLines] = useState<Record<string, number>>({});
  const [scan, setScan] = useState(false);
  const [busy, setBusy] = useState(false);
  const [full, setFull] = useState(false);
  const onCode = useCallback((code: string) => {
    const it = cat?.find((c) => c.barcode === code || c.sku_id === code.toUpperCase());
    const key = it ? it.sku_id : `?${code}`;
    setLines((m) => ({ ...m, [key]: (m[key] ?? 0) + 1 }));
  }, [cat]);
  const photo = async (files: FileList | null) => {
    if (!files?.length) return;
    const f = new FormData();
    Array.from(files).forEach((x) => f.append("files", x));
    f.append("methods", "barcode,ocr,ai");
    setBusy(true);
    try {
      const r = await A.upload<{ lines: { sku_id: string; qty: number }[]; notes: string[] }>(s, "/counts/read-images", f);
      setLines((m) => { const o = { ...m }; r.lines.forEach((l) => { const k = l.sku_id || "?unread"; o[k] = (o[k] ?? 0) + l.qty; }); return o; });
      say(`${r.lines.length} lines read. Check them before saving.`);
    } catch (x) { say((x as Error).message); } finally { setBusy(false); }
  };
  const name = useMemo(() => Object.fromEntries((cat || []).map((c) => [c.sku_id, c.name])), [cat]);
  const known = Object.entries(lines).filter(([k]) => !k.startsWith("?"));
  const save = async () => {
    try {
      const r = await A.post<{ saved: number }>(s, "/counts", { location_id: loc, counted_on: day, scope: full ? "full" : "partial", method: "app",
        lines: known.map(([sku_id, qty]) => ({ sku_id, qty })) }, `count ${loc}`);
      say("queued" in r ? "Offline: count kept on the phone" : `Saved ${r.saved} items for ${loc}`);
      setLines({});
    } catch (x) { say((x as Error).message); }
  };
  const editable = locs?.filter((l) => A.can(me, "track_all") || me?.stores.includes(l.id)) || [];
  return (
    <section>
      {scan && <Scanner onCode={onCode} onClose={() => setScan(false)} />}
      <div className="pair">
        <label>Location<select value={loc} onChange={(e) => setLoc(e.target.value)}><option value="">Choose…</option>{editable.map((l) => <option key={l.id}>{l.id}</option>)}</select></label>
        <label>Stock as of<input type="date" value={day} onChange={(e) => setDay(e.target.value)} /></label>
      </div>
      <label className="check"><input type="checkbox" checked={full} onChange={(e) => setFull(e.target.checked)} /> This is a full count (anything not counted is zero)</label>
      <div className="pair">
        <button className="btn" disabled={!loc} onClick={() => setScan(true)}>📷 Scan barcodes</button>
        <label className={`btn light${!loc || busy ? " dis" : ""}`}>{busy ? "Reading…" : "🧾 Photo of a sheet"}
          <input type="file" accept="image/*,application/pdf" capture="environment" multiple hidden disabled={!loc || busy} onChange={(e) => photo(e.target.files)} /></label>
      </div>
      {Object.entries(lines).map(([k, q]) => (
        <div key={k} className={`card line${k.startsWith("?") ? " diff" : ""}`}>
          <div><b>{k.startsWith("?") ? "Not in product list" : name[k] || k}</b><small>{k.replace(/^\?/, "")}</small></div>
          <input type="number" min={0} inputMode="numeric" value={q} onChange={(e) => setLines({ ...lines, [k]: Math.max(0, Number(e.target.value)) })} />
        </div>
      ))}
      {known.length > 0 && <button className="btn wide sticky" onClick={save}>💾 Save count · {known.length} items · {known.reduce((a, [, q]) => a + q, 0)} units</button>}
    </section>
  );
}

function Issues({ s, me, say }: { s: A.Session; me: A.Perms | null; say: (m: string) => void }) {
  const [x, e, reload] = useLoad(() => A.get<Exc[]>(s, "/exceptions"), [s]);
  const [note, setNote] = useState<Record<number, string>>({});
  if (e) return <div className="err">{e}</div>;
  if (!x) return <Spinner />;
  if (!x.length) return <div className="empty">No open issues. 🎉</div>;
  const resolve = async (id: number) => {
    try { await A.post(s, `/exceptions/${id}/resolve`, { note: note[id] || "" }, `resolve ${id}`); say("Resolved"); reload(); }
    catch (err) { say((err as Error).message); }
  };
  return (
    <section>
      {x.map((i) => (
        <div key={i.id} className={`card issue ${i.severity}`}>
          <b>{i.title}</b><small>{i.region}{i.location ? ` · ${i.location}` : ""} · since {i.first_seen?.slice(0, 10)}</small>
          <p>{i.detail}</p>
          {A.can(me, "resolve") && <div className="pair"><input placeholder="What was done" value={note[i.id] || ""} onChange={(ev) => setNote({ ...note, [i.id]: ev.target.value })} />
            <button className="btn" disabled={(note[i.id] || "").length < 3} onClick={() => resolve(i.id)}>Resolve</button></div>}
        </div>
      ))}
    </section>
  );
}

const Spinner = () => <div className="empty">Loading…</div>;
