import React, { useCallback, useEffect, useState } from "react";

const fmt = (value) => Number(value ?? 0).toLocaleString();
const RELOAD_MS = 30000;

function fmtInstant(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}

function fetchedCount(counts) {
  const fetched = counts?.fetched ?? counts?.claims ?? counts?.clinical;
  if (typeof fetched === "number") return fetched;
  if (!fetched || typeof fetched !== "object") return 0;
  return Object.values(fetched).reduce((total, value) => total + (Number(value) || 0), 0);
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

function ProviderCard({ provider, onReauth }) {
  const summary = (run) => {
    if (!run) return "Never run";
    if (run.status === "running") return "Running now…";
    const counts = run.counts ?? {};
    return `Last run ${run.status} · ${fmt(counts.new_total)} new · ${fmt(fetchedCount(counts))} fetched`;
  };

  const needsAuth = providerNeedsAuth(provider);
  const authMessage = provider.auth.default_only
    ? "This provider has a default token but no identified patient session."
    : "The provider session needs to be connected again before new data can be pulled.";

  return (
    <section className={`card ${needsAuth ? "card-attention" : ""}`}>
      <header className="card-head">
        <div>
          <p className="overline">Connected provider</p>
          <h2>{provider.display_name}</h2>
          <span className={`pill ${provider.kind}`}>{provider.kind}</span>
        </div>
        <button className={`btn ${needsAuth ? "btn-danger" : "btn-soft"}`} onClick={() => onReauth(provider)}>
          {needsAuth ? "Re-authenticate" : "Manage login"}
        </button>
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
        <span className={`run-status ${provider.last_run?.status === "running" ? "is-running" : ""}`}>
          <span className="status-dot" />
          {summary(provider.last_run)}
        </span>
      </div>
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
        <div className="section-heading"><h3>Records</h3><strong className="record-total">{fmt(Object.values(provider.totals).reduce((a, b) => a + b, 0))}</strong></div>
        <ul className="totals">
          {Object.entries(provider.totals).map(([table, count]) => (
            <li key={table}>
              <span className="dim">{table}</span>
              <strong>{fmt(count)}</strong>
            </li>
          ))}
        </ul>
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

export default function App() {
  const [status, setStatus] = useState(null);
  const [error, setError] = useState(null);
  const [reauthProvider, setReauthProvider] = useState(null);
  const [flash, setFlash] = useState(null);

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

  const providers = status ? Object.entries(status.providers).map(([name, p]) => ({ name, ...p })) : [];

  return (
    <div className="app">
      <header className="top">
        <div className="brand-lockup">
          <div className="brand-mark">M</div>
          <div><p className="overline">Personal health data</p><h1>MyHealth</h1></div>
        </div>
        <button className="btn btn-soft" onClick={load}>
          Refresh
        </button>
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
          <ProviderCard key={p.name} provider={p} onReauth={setReauthProvider} />
        ))}
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
    </div>
  );
}
