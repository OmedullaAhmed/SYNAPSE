// StockFlow API client: server address, sign-in token, and an offline outbox for writes made without signal.

export type Session = { server: string; token: string; user: string; role: string; exp: number };
export type Perms = { user: string; role: string; stores: string[]; permissions: string[] };

const KEY = "sf.session";
const OUTBOX = "sf.outbox";

export function defaultServer(): string {
  // Served by the API itself (PWA at /app/): same origin. Inside the Android / iOS shell: the user types the server once.
  const { protocol, host } = window.location;
  return protocol.startsWith("http") && !host.startsWith("localhost") ? `${protocol}//${host}` : "";
}

export function loadSession(): Session | null {
  try {
    const s = JSON.parse(localStorage.getItem(KEY) || "null") as Session | null;
    return s && s.exp > Date.now() ? s : null;
  } catch {
    return null;
  }
}

export function saveSession(s: Session | null) {
  if (s) localStorage.setItem(KEY, JSON.stringify(s));
  else localStorage.removeItem(KEY);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, msg: string) { super(msg); this.status = status; }
}

async function call<T>(s: Session, path: string, init: RequestInit = {}): Promise<T> {
  const r = await fetch(`${s.server}/api/v1${path}`, {
    ...init,
    headers: { ...(init.body instanceof FormData ? {} : { "Content-Type": "application/json" }), Authorization: `Bearer ${s.token}`, ...(init.headers || {}) },
  });
  if (!r.ok) {
    let msg = `${r.status}`;
    try { msg = (await r.json()).detail || msg; } catch { /* not JSON */ }
    if (r.status === 401) saveSession(null);
    throw new ApiError(r.status, typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return r.json() as Promise<T>;
}

export async function signIn(server: string, username: string, password: string): Promise<Session> {
  const base = server.replace(/\/+$/, "");
  const r = await fetch(`${base}/api/v1/auth/token`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username, password }) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new ApiError(r.status, j.detail || "Sign-in failed");
  const s: Session = { server: base, token: j.access_token, user: j.user, role: j.role, exp: Date.now() + (j.expires_in - 300) * 1000 };
  saveSession(s);
  return s;
}

export const get = <T,>(s: Session, path: string) => call<T>(s, path);

// ---- writes: sent now, or kept in the outbox when the phone is offline and sent when it is back online
type Pending = { path: string; body: string; at: number; label: string };

export function outbox(): Pending[] {
  try { return JSON.parse(localStorage.getItem(OUTBOX) || "[]"); } catch { return []; }
}

function setOutbox(p: Pending[]) { localStorage.setItem(OUTBOX, JSON.stringify(p)); }

export async function post<T>(s: Session, path: string, body: unknown, label = ""): Promise<T | { queued: true }> {
  const json = JSON.stringify(body);
  try {
    return await call<T>(s, path, { method: "POST", body: json });
  } catch (e) {
    if (e instanceof ApiError) throw e;                         // the server answered: a real error, don't queue
    setOutbox([...outbox(), { path, body: json, at: Date.now(), label }]);
    return { queued: true };
  }
}

export async function flush(s: Session): Promise<{ sent: number; failed: string[] }> {
  const left: Pending[] = [];
  const failed: string[] = [];
  let sent = 0;
  for (const p of outbox()) {
    try {
      await call(s, p.path, { method: "POST", body: p.body });
      sent++;
    } catch (e) {
      if (e instanceof ApiError) failed.push(`${p.label || p.path}: ${e.message}`);   // refused: drop it, tell the user
      else left.push(p);                                                                // still offline: keep
    }
  }
  setOutbox(left);
  return { sent, failed };
}

export async function upload<T>(s: Session, path: string, form: FormData): Promise<T> {
  return call<T>(s, path, { method: "POST", body: form });
}

export const can = (p: Perms | null, perm: string) => !!p && p.permissions.includes(perm);
