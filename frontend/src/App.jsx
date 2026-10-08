import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { API, AuthError, api, clearToken, getToken, setToken } from "./api.js";

const AUTH_ERRORS = {
  auth_failed: "Google sign-in didn't complete. Try again.",
  token_exchange: "Google rejected the sign-in. Check your OAuth client settings.",
  gmail_permission: "Gmail access wasn't granted. Tick the Gmail permission on the Google screen to continue.",
  no_refresh_token: "Google didn't return offline access. Remove the app at myaccount.google.com/permissions and connect again.",
};

const fmt = (n) => n.toLocaleString("en-IN");
const ago = (iso) => {
  if (!iso) return "never";
  const days = Math.floor((Date.now() - new Date(iso)) / 86400000);
  if (days < 1) return "today";
  if (days < 30) return `${days}d ago`;
  if (days < 365) return `${Math.floor(days / 30)}mo ago`;
  return `${Math.floor(days / 365)}y ago`;
};
const openRate = (s) => (s.msg_count ? Math.round(((s.msg_count - s.unread_count) / s.msg_count) * 100) : 0);

export default function App() {
  const [token, setTok] = useState(() => getToken());
  const [authError, setAuthError] = useState(null);

  useEffect(() => {
    const hash = new URLSearchParams(window.location.hash.slice(1));
    if (hash.get("token")) {
      setToken(hash.get("token"));
      setTok(hash.get("token"));
    }
    if (hash.get("error")) setAuthError(AUTH_ERRORS[hash.get("error")] || "Sign-in failed.");
    if (window.location.hash) history.replaceState(null, "", window.location.pathname);
  }, []);

  const signOut = useCallback(() => {
    clearToken();
    setTok(null);
  }, []);

  return token ? <Dashboard onSignOut={signOut} /> : <Landing error={authError} />;
}

/* ------------------------------------------------------------------ landing */

const SAMPLE = [
  { name: "Daily Deals", email: "offers@dealsdaily.example", n: 312, read: 2 },
  { name: "Quick Bite", email: "hello@quickbite.example", n: 187, read: 9 },
  { name: "The Weekly Digest", email: "news@weeklydigest.example", n: 96, read: 41 },
];

function Landing({ error }) {
  return (
    <main className="landing">
      <section className="landing-copy">
        <p className="wordmark">Inbox Cleaner</p>
        <h1>Most of your inbox is mail you never opened.</h1>
        <p className="lede">
          Connect Gmail and see every sender ranked by how much they send. Unsubscribe or clear them out in one click.
        </p>
        {error && <p className="notice error" role="alert">{error}</p>}
        <a className="btn primary big" href={`${API}/auth/google/start`}>Connect Gmail</a>
        <ul className="promises">
          <li>Reads sender headers only, never message bodies.</li>
          <li>Deleted mail goes to Trash, so you can undo for 30 days.</li>
          <li>Disconnect and erase your data any time.</li>
        </ul>
      </section>

      <section className="landing-sample" aria-label="Example of the sender list">
        {SAMPLE.map((s) => (
          <div className="sample-row" key={s.email}>
            <div className="who">
              <strong>{s.name}</strong>
              <span>{s.email}</span>
            </div>
            <div className="bar" aria-hidden="true"><i style={{ width: `${(s.n / 312) * 100}%` }} /></div>
            <div className="num">{s.n} emails, opened {s.read}%</div>
          </div>
        ))}
      </section>
    </main>
  );
}

/* ---------------------------------------------------------------- dashboard */

const TABS = [
  { id: "review", label: "To review" },
  { id: "unsubscribed", label: "Unsubscribed" },
  { id: "kept", label: "Kept" },
];
const SORTS = {
  volume: { label: "Most emails", fn: (a, b) => b.msg_count - a.msg_count },
  unread: { label: "Least opened", fn: (a, b) => openRate(a) - openRate(b) || b.msg_count - a.msg_count },
  recent: { label: "Most recent", fn: (a, b) => new Date(b.last_received || 0) - new Date(a.last_received || 0) },
};

function Dashboard({ onSignOut }) {
  const [me, setMe] = useState(null);
  const [senders, setSenders] = useState([]);
  const [tab, setTab] = useState("review");
  const [sort, setSort] = useState("volume");
  const [q, setQ] = useState("");
  const [notes, setNotes] = useState({}); // sender id -> { text, url }
  const [busy, setBusy] = useState({}); // sender id -> true
  const [confirming, setConfirming] = useState(null);
  const [error, setError] = useState(null);
  const started = useRef(false);

  const fail = useCallback(
    (e) => {
      if (e instanceof AuthError) onSignOut();
      else setError(e.message);
    },
    [onSignOut]
  );

  const loadSenders = useCallback(() => api.senders().then(setSenders).catch(fail), [fail]);

  // Poll account status while scanning.
  useEffect(() => {
    let timer;
    const tick = async () => {
      try {
        const m = await api.me();
        setMe(m);
        if (m.scan_status === "scanning") timer = setTimeout(tick, 1500);
        else if (m.scan_status === "idle" && !started.current) {
          started.current = true;
          await api.scan();
          timer = setTimeout(tick, 800);
        } else loadSenders();
      } catch (e) {
        fail(e);
      }
    };
    tick();
    return () => clearTimeout(timer);
  }, [fail, loadSenders]);

  // Poll while any delete is running.
  const anyDeleting = senders.some((s) => s.status === "deleting");
  useEffect(() => {
    if (!anyDeleting) return;
    const t = setInterval(loadSenders, 2000);
    return () => clearInterval(t);
  }, [anyDeleting, loadSenders]);

  const patch = (id, changes) => setSenders((list) => list.map((s) => (s.id === id ? { ...s, ...changes } : s)));
  const withBusy = async (id, fn) => {
    setBusy((b) => ({ ...b, [id]: true }));
    setError(null);
    try {
      await fn();
    } catch (e) {
      fail(e);
    } finally {
      setBusy((b) => ({ ...b, [id]: false }));
    }
  };

  const doUnsubscribe = (s) =>
    withBusy(s.id, async () => {
      const r = await api.unsubscribe(s.id);
      if (r.result === "ok") {
        patch(s.id, { status: "unsubscribed" });
        setNotes((n) => ({ ...n, [s.id]: { text: r.method === "email" ? "Unsubscribe email sent." : "Unsubscribed." } }));
      } else if (r.result === "manual") {
        setNotes((n) => ({ ...n, [s.id]: { text: "This sender needs you to confirm on their site.", url: r.url } }));
      } else {
        setNotes((n) => ({ ...n, [s.id]: { text: "No unsubscribe option found. Delete their mail instead." } }));
      }
    });

  const doDelete = (s) => {
    setConfirming(null);
    return withBusy(s.id, async () => {
      await api.remove(s.id);
      patch(s.id, { status: "deleting" });
    });
  };

  const doKeep = (s) =>
    withBusy(s.id, async () => {
      await api.keep(s.id);
      patch(s.id, { status: "kept" });
    });

  const rescan = async () => {
    started.current = true;
    await api.scan().catch(fail);
    setMe((m) => ({ ...m, scan_status: "scanning", scanned: 0, total: 0 }));
    // restart polling by reloading status
    const poll = async () => {
      const m = await api.me().catch(fail);
      if (!m) return;
      setMe(m);
      if (m.scan_status === "scanning") setTimeout(poll, 1500);
      else loadSenders();
    };
    setTimeout(poll, 800);
  };

  const eraseAccount = async () => {
    if (!window.confirm("Disconnect Gmail and erase everything stored about you? Your emails are not touched.")) return;
    await api.deleteAccount().catch(fail);
    onSignOut();
  };

  const stats = useMemo(() => {
    const live = senders.filter((s) => s.status !== "deleted");
    const total = live.reduce((a, s) => a + s.msg_count, 0);
    const unread = live.reduce((a, s) => a + s.unread_count, 0);
    const removed = senders.filter((s) => s.status === "deleted").length;
    return { total, senders: live.length, open: total ? Math.round(((total - unread) / total) * 100) : 0, removed };
  }, [senders]);

  const visible = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return senders
      .filter((s) => s.status !== "deleted")
      .filter((s) => (tab === "review" ? ["active", "deleting"].includes(s.status) : s.status === tab))
      .filter((s) => !needle || s.from_email.includes(needle) || (s.from_name || "").toLowerCase().includes(needle))
      .sort(SORTS[sort].fn);
  }, [senders, tab, sort, q]);

  const maxCount = Math.max(1, ...visible.map((s) => s.msg_count));
  const scanning = me?.scan_status === "scanning";
  const pct = me?.total ? Math.min(100, Math.round((me.scanned / me.total) * 100)) : 4;

  return (
    <main className="dash">
      <header className="topbar">
        <p className="wordmark">Inbox Cleaner</p>
        <div className="topbar-right">
          <span className="acct">{me?.email}</span>
          <button className="btn quiet" onClick={rescan} disabled={scanning}>Rescan</button>
          <button className="btn quiet" onClick={onSignOut}>Sign out</button>
        </div>
      </header>

      {error && <p className="notice error" role="alert">{error}</p>}
      {me?.scan_status === "error" && (
        <p className="notice error" role="alert">
          The scan failed: {me.scan_error || "unknown error"}. If Google access was revoked, sign out and connect again.
        </p>
      )}

      {scanning ? (
        <section className="scan">
          <h2>Reading your inbox</h2>
          <div className="progress" role="progressbar" aria-valuenow={pct} aria-valuemin="0" aria-valuemax="100">
            <i style={{ width: `${pct}%` }} />
          </div>
          <p>
            {me.total ? `${fmt(me.scanned)} of ${fmt(me.total)} emails checked` : "Finding newsletters and promotions"}
          </p>
        </section>
      ) : (
        <>
          <section className="summary">
            <h1>
              {fmt(stats.total)} emails from {fmt(stats.senders)} senders. You opened {stats.open}% of them.
            </h1>
            {stats.removed > 0 && (
              <p>
                {stats.removed} sender{stats.removed > 1 ? "s" : ""} cleared. Trash empties itself after 30 days.
              </p>
            )}
          </section>

          <section className="controls">
            <div className="tabs" role="tablist">
              {TABS.map((t) => (
                <button key={t.id} role="tab" aria-selected={tab === t.id} className={tab === t.id ? "on" : ""} onClick={() => setTab(t.id)}>
                  {t.label}
                </button>
              ))}
            </div>
            <input type="search" placeholder="Search senders" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search senders" />
            <select value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Sort senders">
              {Object.entries(SORTS).map(([k, v]) => (
                <option key={k} value={k}>{v.label}</option>
              ))}
            </select>
          </section>

          <ul className="ledger">
            {visible.length === 0 && (
              <li className="empty">
                {tab === "review" ? "Nothing left to review. Rescan any time to catch new senders." : "Nothing here yet."}
              </li>
            )}
            {visible.map((s) => {
              const note = notes[s.id];
              const deleting = s.status === "deleting";
              return (
                <li className="row" key={s.id}>
                  <div className="who">
                    <strong>{s.from_name || s.from_email}</strong>
                    <span>{s.from_name ? s.from_email : "\u00a0"}</span>
                  </div>
                  <div className="vol">
                    <div className="bar" aria-hidden="true"><i style={{ width: `${(s.msg_count / maxCount) * 100}%` }} /></div>
                    <span>
                      {fmt(s.msg_count)} emails, opened {openRate(s)}%, last {ago(s.last_received)}
                    </span>
                  </div>
                  <div className="actions">
                    {deleting ? (
                      <span className="chip">Moving to Trash…</span>
                    ) : confirming === s.id ? (
                      <>
                        <button className="btn danger" onClick={() => doDelete(s)}>Trash {fmt(s.msg_count)} emails</button>
                        <button className="btn quiet" onClick={() => setConfirming(null)}>Cancel</button>
                      </>
                    ) : (
                      <>
                        {s.status !== "unsubscribed" && s.can_unsubscribe && (
                          <button className="btn primary" disabled={busy[s.id]} onClick={() => doUnsubscribe(s)}>
                            {busy[s.id] ? "Working…" : "Unsubscribe"}
                          </button>
                        )}
                        {s.status === "unsubscribed" && <span className="chip ok">Unsubscribed</span>}
                        <button className="btn outline" disabled={busy[s.id]} onClick={() => setConfirming(s.id)}>Delete mail</button>
                        {s.status !== "kept" && (
                          <button className="btn quiet" disabled={busy[s.id]} onClick={() => doKeep(s)}>Keep</button>
                        )}
                      </>
                    )}
                  </div>
                  {note && (
                    <p className="note">
                      {note.text}{" "}
                      {note.url && (
                        <a href={note.url} target="_blank" rel="noopener noreferrer">Open unsubscribe page</a>
                      )}
                    </p>
                  )}
                </li>
              );
            })}
          </ul>

          <footer className="foot">
            <button className="btn quiet" onClick={eraseAccount}>Disconnect Gmail and erase my data</button>
          </footer>
        </>
      )}
    </main>
  );
}
