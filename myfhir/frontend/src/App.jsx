import React, { useCallback, useEffect, useRef, useState } from "react";
import { useReactTable, getCoreRowModel, getSortedRowModel, getFilteredRowModel, flexRender } from "@tanstack/react-table";

const fmt = (value) => Number(value ?? 0).toLocaleString();
const RELOAD_MS = 30000;

function fmtInstant(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, { timeZoneName: "short" });
}

function timeAgo(iso) {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const sec = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (sec < 60) return `${sec} seconds ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} minute${min === 1 ? "" : "s"} ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} hour${hr === 1 ? "" : "s"} ago`;
  const day = Math.floor(hr / 24);
  return `${day} day${day === 1 ? "" : "s"} ago`;
}

function fetchedCount(counts) {
  const fetched = counts?.fetched ?? counts?.claims ?? counts?.clinical;
  if (typeof fetched === "number") return fetched;
  if (!fetched || typeof fetched !== "object") return 0;
  return Object.values(fetched).reduce((total, value) => total + (Number(value) || 0), 0);
}

const ACRONYMS = { eob: "EOB", dx: "DX", rx: "RX" };
function tableLabel(table) {
  return String(table ?? "")
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((word) => ACRONYMS[word.toLowerCase()] ?? word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

function syncProgressText(progress) {
  if (progress.stage === "starting") return "Starting sync…";
  return [
    progress.stage,
    progress.total ? `${progress.index}/${progress.total} patients` : "",
    progress.message || "",
    progress.elapsed_sec != null ? `${progress.elapsed_sec}s` : "",
  ]
    .filter(Boolean)
    .join(" · ");
}

function providerNeedsAuth(provider) {
  return provider.auth.reauth_needed || provider.auth.default_only;
}

function TokenBadge({ patient }) {
  const map = {
    valid: ["ok", "Valid"],
    expiring: ["warn", "Near expiry"],
    expired: ["bad", "Expired"],
    reauth: ["bad", "Authentication expired"],
    setup: ["warn", "Patient login needed"],
  };
  const [cls, label] = map[patient.state] ?? ["dim", patient.state];
  return (
    <span className={`badge ${cls}`} title={patient.reason ?? ""}>
      {label}
    </span>
  );
}

function ProviderCard({ provider, onReauth, onSync, syncing, progress }) {
  const summary = (run) => {
    if (!run) return "Never run";
    if (run.status === "running") return "Running now…";
    const counts = run.counts ?? {};
    const ago = timeAgo(run.finished_at);
    return `Last run ${run.status} · ${fmt(counts.new_total)} new · ${fmt(fetchedCount(counts))} fetched${ago ? ` · ${ago}` : ""}`;
  };

  const needsAuth = providerNeedsAuth(provider);
  const authMessage = provider.auth.default_only
    ? "This provider has a default token but no identified patient session."
    : "The provider session needs to be connected again before new data can be pulled.";

  const recordRows = Object.entries(provider.totals ?? {}).filter(([, count]) => (count ?? 0) > 0);
  const recordTotal = recordRows.reduce((sum, [, count]) => sum + (count ?? 0), 0);

  return (
    <section className={`card ${needsAuth ? "card-attention" : ""}`}>
      <header className="card-head">
        <div>
          <p className="overline">Connected provider</p>
          <h2>{provider.display_name}</h2>
          <span className={`pill ${provider.kind}`}>{provider.kind}</span>
        </div>
        <div className="card-actions">
          <button className="btn btn-soft" onClick={() => onSync(provider)} disabled={syncing} title="Run a fresh data sync now">
            {syncing ? "Syncing…" : "Sync now"}
          </button>
          <button className={`btn ${needsAuth ? "btn-danger" : "btn-soft"}`} onClick={() => onReauth(provider)}>
            {needsAuth ? "Re-authenticate" : "Manage login"}
          </button>
        </div>
      </header>

      {needsAuth && (
        <div className="auth-alert" role="alert">
          <div className="alert-icon">!</div>
          <div className="auth-alert-copy">
            <strong>{provider.auth.default_only ? "Patient authentication needed" : "Authentication expired"}</strong>
            <span>{authMessage}</span>
          </div>
          <button className="btn btn-danger btn-alert" onClick={() => onReauth(provider)}>
            Fix now
          </button>
        </div>
      )}

      <div className="run-line">
        <span className={`run-status ${provider.last_run?.status === "running" ? "is-running" : provider.last_run?.status === "failed" ? "is-failed" : ""}`}>
          <span className="status-dot" />
          {summary(provider.last_run)}
        </span>
      </div>
      {progress && progress.status === "running" && (
        <div className="sync-progress" role="status" aria-live="polite">
          <span className="spinner" aria-hidden="true" />
          <span className="sync-progress-text">{syncProgressText(progress)}</span>
        </div>
      )}
      {provider.last_run?.finished_at && (
        <p className="run-line dim">
          Finished {fmtInstant(provider.last_run.finished_at)}
          {provider.last_run.error ? ` · error: ${provider.last_run.error}` : ""}
        </p>
      )}

      <div className="grid">
        <div className="section-heading"><h3>Access</h3><span>{provider.auth.patients.length} session{provider.auth.patients.length === 1 ? "" : "s"}</span></div>
        {provider.auth.patients.length === 0 ? (
          <p className="dim">Not authenticated — no token stored.</p>
        ) : (
          <ul className="patients">
            {provider.auth.patients.map((p) => (
              <li key={p.patient_id}>
                <span className="patient-name">{p.patient_name || p.patient_id}</span>
                <TokenBadge patient={p} />
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="grid">
        <div className="section-heading"><h3>Records</h3><strong className="record-total">{fmt(recordTotal)}</strong></div>
        {recordRows.length === 0 ? (
          <p className="dim">No records synced yet.</p>
        ) : (
          <ul className="totals">
            {recordRows.map(([table, count]) => (
              <li key={table}>
                <span className="dim">{tableLabel(table)}</span>
                <strong>{fmt(count)}</strong>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

function AuthModal({ provider, onClose, onDone }) {
  const [url, setUrl] = useState(null);
  const [state, setState] = useState(null);
  const [paste, setPaste] = useState("");
  const [error, setError] = useState(null);

  const begin = async () => {
    setError(null);
    const popup = window.open("about:blank", "myhealth-provider-login");
    try {
      const res = await fetch(`/api/auth/${provider.name}/start`);
      if (!res.ok) throw new Error(`Failed to start auth: ${res.status}`);
      const data = await res.json();
      setUrl(data.authorize_url);
      setState(data.state);
      if (popup) popup.location.href = data.authorize_url;
    } catch (e) {
      popup?.close();
      setError(String(e));
    }
  };

  const exchange = async () => {
    setError(null);
    const res = await fetch(`/api/auth/${provider.name}/exchange`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ redirect_url: paste.trim() }),
    });
    const data = await res.json();
    if (!res.ok) {
      setError(data.detail || "Exchange failed");
      return;
    }
    onDone(data);
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <p className="overline">Secure connection</p>
        <h2>Connect {provider.display_name}</h2>
        {!url ? (
          <button className="btn btn-primary btn-wide" onClick={begin}>
            Open consent screen
          </button>
        ) : (
          <>
            <p>Approve access in the new tab. After approval, the browser will redirect to the
            configured callback URL that contains <code>code=…</code>. Copy the full
            address bar URL and paste it below.</p>
            <p className="dim">
              <a href={url} target="_blank" rel="noreferrer">
                Re-open consent screen
              </a>
            </p>
            <textarea
              rows="3"
              placeholder="https://host/callback?code=…"
              value={paste}
              onChange={(e) => setPaste(e.target.value)}
            />
            <div className="modal-actions">
              {state && <span className="dim pill">{state.slice(0, 8)}…</span>}
              <button className="btn btn-primary" onClick={exchange} disabled={!paste.trim()}>
                Exchange Code
              </button>
            </div>
          </>
        )}
        {error && <p className="error">{error}</p>}
        <button className="btn ghost" onClick={onClose}>
          Close
        </button>
      </div>
    </div>
  );
}

/* ─── Member Claims Modal ─────────────────────────────────────── */
function SubmitClaimModal({ onClose, onDone }) {
  const [claimNumber, setClaimNumber] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const submit = async (e) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const res = await fetch("/api/claims/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ claim_number: claimNumber }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.detail || "Registration failed");
        setLoading(false);
        return;
      }
      onDone(data);
    } catch (e) {
      setError(String(e));
      setLoading(false);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 420 }}>
        <p className="overline">Flag as member-submitted</p>
        <h2>Member Claim</h2>
        <p className="dim">Paste a claim number from Anthem's portal. Everything else comes from FHIR.</p>

        {error && <p className="error">{error}</p>}

        <form onSubmit={submit}>
          <label>
            <span>Claim Number</span>
            <input required autoFocus value={claimNumber} onChange={(e) => setClaimNumber(e.target.value)} placeholder="20262502A2197" />
          </label>
          <div className="modal-actions">
            <button type="button" className="btn ghost" onClick={onClose} disabled={loading}>
              Cancel
            </button>
            <button className="btn btn-primary" type="submit" disabled={loading}>
              {loading ? "Flagging…" : "Flag as Member"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

/* ─── Member Claims Grid ──────────────────────────────────────── */
function MemberClaimsGrid({ onOpenSubmit }) {
  const [claims, setClaims] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [globalFilter, setGlobalFilter] = useState("");

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/claims/member-submitted");
      if (!res.ok) throw new Error(`status ${res.status}`);
      const data = await res.json();
      setClaims(data);
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const t = setInterval(load, RELOAD_MS);
    return () => clearInterval(t);
  }, [load]);

  /* ── Hooks MUST be called before all early returns ─────────── */
  const fmtMoney = (v) => v != null ? `$${Number(v).toLocaleString()}` : "—";

  const columns = [
    { header: "Claim #", accessorKey: "claim_number", size: 130 },
    { header: "Date", accessorKey: "serviced_date", size: 100, cell: (info) => info.getValue() || "—" },
    { header: "Status", accessorKey: "status", size: 100 },
    { header: "Provider", accessorKey: "provider_name", size: 160 },
    { header: "CPT", accessorKey: "hcpcs_code", size: 100, cell: (info) => info.getValue() || "—" },
    { header: "Billed", accessorKey: "submitted_amount", size: 90, cell: (info) => fmtMoney(info.getValue()) },
    { header: "Allowed", accessorKey: "allowed_amount", size: 90, cell: (info) => fmtMoney(info.getValue()) },
    { header: "Paid Plan", accessorKey: "paid_provider", size: 90, cell: (info) => fmtMoney(info.getValue()) },
    { header: "Paid Patient", accessorKey: "paid_patient", size: 90, cell: (info) => fmtMoney(info.getValue()) },
    { header: "Noncovered", accessorKey: "item_noncovered", size: 90, cell: (info) => fmtMoney(info.getValue()) },
    { header: "Liability", accessorKey: "member_liability", size: 90, cell: (info) => fmtMoney(info.getValue()) },
    { header: "Reason", accessorKey: "adjustment_reason", size: 140 },
  ];

  const table = useReactTable({
    data: claims?.claims ?? [],
    columns,
    state: { globalFilter },
    onGlobalFilterChange: setGlobalFilter,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
  });

  /* ── Now it's safe to return early ─────────────────────────── */
  if (loading) return <p className="dim">Loading member claims…</p>;
  if (error) return <p className="error">{error}</p>;
  if (!claims || claims.total === 0) return (
    <div className="claims-empty">
      <p>No member-submitted claims flagged yet.</p>
      <button className="btn btn-primary" onClick={onOpenSubmit}>Flag a claim</button>
    </div>
  );

  const totalBilled = claims.claims.reduce((s, c) => s + (c.submitted_amount || 0), 0);

  return (
    <section className="card">
      <header className="card-head">
        <div>
          <p className="overline">Year {claims.year}</p>
          <h2>Member-Submitted Claims</h2>
          <span className="dim">{claims.total} line{claims.total !== 1 ? "s" : ""} · ${fmt(totalBilled)} billed</span>
        </div>
        <div className="card-actions">
          <button className="btn btn-soft" onClick={load}>Refresh</button>
          <button className="btn btn-primary" onClick={onOpenSubmit}>+ Flag</button>
        </div>
      </header>

      <div className="claims-toolbar">
        <input
          className="claims-search"
          placeholder="Filter claims…"
          value={globalFilter ?? ""}
          onChange={(e) => setGlobalFilter(String(e.target.value))}
        />
        <span className="dim">{table.getFilteredRowModel().rows.length} rows</span>
      </div>

      <div className="claims-table-wrap">
        <table className="claims-table">
          <thead>
            {table.getHeaderGroups().map(hg => (
              <tr key={hg.id}>
                {hg.headers.map(h => (
                  <th key={h.id} style={{ width: h.getSize() }}>
                    {h.column.getCanSort() ? (
                      <span className="claims-th" onClick={h.column.getToggleSortingHandler()}>
                        {flexRender(h.column.columnDef.header, h.getContext())}{" "}
                        {h.column.getIsSorted() === "desc" ? "\u25BC" : h.column.getIsSorted() === "asc" ? "\u25B2" : "\u21D5"}
                      </span>
                    ) : flexRender(h.column.columnDef.header, h.getContext())}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.map(row => (
              <tr key={row.id}>
                {row.getVisibleCells().map(cell => (
                  <td key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/* ─── Debug Banner ────────────────────────────────────────────── */
function DebugBanner() {
  const [debug, setDebug] = useState(null);
  useEffect(() => {
    setDebug({
      ts: new Date().toISOString(),
      url: window.location.href,
      res: typeof React,
    });
  }, []);
  if (!debug) return null;
  return (
    <div style={{ position: "fixed", bottom: 0, left: 0, right: 0, background: "#1a1f2e", color: "#fff", padding: "8px 16px", fontSize: 12, fontFamily: "monospace", zIndex: 9999 }}>
      <span>{debug.ts} | {debug.url} | React={debug.res}</span>
      <button onClick={() => { fetch("/api/status").then(r => r.json()).then(d => console.log("STATUS:", d)); alert("API status logged to console"); }} style={{ marginLeft: 12, cursor: "pointer" }}>
        Test API
      </button>
    </div>
  );
}

/* ─── Dashboard App ───────────────────────────────────────────── */
export default function App() {
  const [status, setStatus] = useState(null);
  const [error, setError] = useState(null);
  const [reauthProvider, setReauthProvider] = useState(null);
  const [flash, setFlash] = useState(null);
  const [pendingSync, setPendingSync] = useState({});
  const [syncProgress, setSyncProgress] = useState({});
  const esRef = useRef({});

  const closeStream = (name) => {
    const es = esRef.current[name];
    if (es) {
      es.close();
      delete esRef.current[name];
    }
  };

  useEffect(() => () => Object.values(esRef.current).forEach((es) => es.close()), []);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/status");
      if (!res.ok) throw new Error(`status ${res.status}`);
      setStatus(await res.json());
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, RELOAD_MS);
    return () => clearInterval(t);
  }, [load]);

  useEffect(() => {
    if (!status) return;
    setPendingSync((prev) => {
      const next = { ...prev };
      let changed = false;
      for (const name of Object.keys(prev)) {
        const run = status.providers[name]?.last_run;
        if (!run || run.status !== "running") {
          delete next[name];
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [status]);

  const startSync = async (provider) => {
    setFlash(null);
    try {
      const res = await fetch(`/api/sync/${provider.name}`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) {
        setFlash(data.detail || `Sync failed for ${provider.display_name}`);
        return;
      }
      setPendingSync((p) => ({ ...p, [provider.name]: true }));
      setSyncProgress((p) => ({
        ...p,
        [provider.name]: { status: "running", stage: "starting", index: 0, total: 0, message: "" },
      }));
      closeStream(provider.name);
      const es = new EventSource(`/api/sync/${provider.name}/progress`);
      esRef.current[provider.name] = es;
      es.onmessage = (e) => {
        let snap;
        try { snap = JSON.parse(e.data); } catch { return; }
        setSyncProgress((p) => ({ ...p, [provider.name]: snap }));
        if (snap.status === "done" || snap.status === "failed" || snap.status === "none") {
          closeStream(provider.name);
          if (snap.status === "done") setFlash(`Sync complete for ${provider.display_name}`);
          else if (snap.status === "failed")
            setFlash(`Sync failed for ${provider.display_name}${snap.message ? `: ${snap.message}` : ""}`);
          load();
        }
      };
      setFlash(`Sync started for ${provider.display_name}`);
    } catch (e) {
      setFlash(`Sync failed: ${e}`);
    }
    load();
  };

  const isSyncing = (p) => p.last_run?.status === "running" || !!pendingSync[p.name];
  const providers = status ? Object.entries(status.providers).map(([name, p]) => ({ name, ...p })) : [];

  // Member claims state
  const [showSubmitModal, setShowSubmitModal] = useState(false);

  return (
    <div className="app">
      <header className="top">
        <div className="brand-lockup">
          <div className="brand-mark">M</div>
          <div><p className="overline">Personal health data</p><h1>MyHealth</h1></div>
        </div>
        <div className="top-actions">
          <button className="btn btn-soft" onClick={() => setShowSubmitModal(true)} title="Register a manually-submitted claim">
            Member Claims
          </button>
          <button className="btn btn-soft" onClick={load} title="Refresh dashboard data">
            Refresh
          </button>
        </div>
      </header>

      <section className="hero">
        <div><p className="overline">Overview</p><h2>Your health data, in one place.</h2><p>Secure connections to your insurance and clinical providers.</p></div>
        <div className="last-updated">{status ? `Updated ${fmtInstant(status.generated_at)}` : "Loading status…"}</div>
      </section>

      {error && <div className="banner error">Backend unreachable — {error}</div>}
      {!error && status && providers.length && providers.some((p) => p.auth.reauth_needed) && (
        <div className="banner warn">
          One or more provider connections need your attention. Reconnect them below to resume data pulls.
        </div>
      )}
      {flash && <div className="banner ok">{flash}</div>}

      <main className="cards">
        {providers.map((p) => (
          <ProviderCard
            key={p.name}
            provider={p}
            onReauth={setReauthProvider}
            onSync={startSync}
            syncing={isSyncing(p)}
            progress={syncProgress[p.name]}
          />
        ))}
        <MemberClaimsGrid onOpenSubmit={() => setShowSubmitModal(true)} />
      </main>

      {reauthProvider && (
        <AuthModal
          provider={reauthProvider}
          onClose={() => setReauthProvider(null)}
          onDone={({ patient_id, patient_name }) => {
            setFlash(`Token stored for ${patient_name || patient_id}`);
            setReauthProvider(null);
            load();
          }}
        />
      )}

      {showSubmitModal && (
        <SubmitClaimModal
          onClose={() => setShowSubmitModal(false)}
          onDone={(data) => {
            setFlash(`Claim ${data.claim_number} flagged (EOB: ${data.eob_id || "—"})`);
            setShowSubmitModal(false);
          }}
        />
      )}

      <DebugBanner />
    </div>
  );
}
