import { useState, useEffect, useRef } from "react";

// NAV
const PAGES_TOP = [
  { id: "live", label: "LIVE" },
  { id: "library", label: "LIBRARY INDEX" },
  { id: "announcements", label: "ANNOUNCE & EVENTS" },
  { id: "logs", label: "LOGS & REVIEW" },
  { id: "providers", label: "PROVIDERS & COST" },
  { id: "culture", label: "CULTURAL NOTES" },
  { id: "snapshot", label: "CULTURAL SNAPSHOT" },
  { id: "senses", label: "SENSES" },
  { id: "governance", label: "SETTINGS & GOV" },
];

const PAGES_BOTTOM = [
  { id: "studio", label: "STUDIO PROFILE" },
  { id: "auth", label: "AUTH & ACCOUNTS" },
  { id: "nowplaying", label: "NOW PLAYING" },
];

const STATUS_COLORS = {
  ACTIVE: "#2ecc40",
  INACTIVE: "#ff851b",
  SILENCED: "#ff4136",
};

const API_BASE =
  (typeof window !== "undefined" && window.__ROONIE_DASHBOARD_API__) ||
  (typeof window !== "undefined"
    ? `${window.location.protocol}//${window.location.hostname}:8787`
    : "http://127.0.0.1:8787");
const OPERATOR_KEY_STORAGE_KEY = "ROONIE_OPERATOR_KEY";
const OPERATOR_ACTOR_STORAGE_KEY = "ROONIE_OPERATOR_ACTOR";
const LAST_LOGIN_USERNAME_STORAGE_KEY = "ROONIE_LAST_LOGIN_USERNAME";
const REMEMBER_LOGIN_STORAGE_KEY = "ROONIE_REMEMBER_LOGIN";
const OPERATOR_KEY =
  (typeof window !== "undefined" && (window.__ROONIE_OPERATOR_KEY__ || window.localStorage.getItem(OPERATOR_KEY_STORAGE_KEY))) || "";
const INITIAL_OPERATOR_ACTOR =
  (typeof window !== "undefined" && (window.__ROONIE_OPERATOR_ACTOR__ || window.localStorage.getItem(OPERATOR_ACTOR_STORAGE_KEY))) || "";

async function apiFetch(url, options = {}) {
  const opts = { ...options, credentials: "include" };
  return fetch(url, opts);
}

const EMPTY_STATUS = {
  kill_switch_on: true,
  armed: false,
  mode: "offline",
  twitch_connected: false,
  last_heartbeat_at: null,
  active_provider: "none",
  version: "unknown",
  policy_loaded_at: null,
  policy_version: null,
  context_last_active: false,
  context_last_turns_used: 0,
  silenced: false,
  silence_until: null,
  read_only_mode: false,
  active_director: "ProviderDirector",
  routing_enabled: true,
};

const AWAITING = "Awaiting data...";
const TEXT_STYLES = {
  body: {
    fontSize: 12,
    color: "#aaa",
    fontFamily: "'IBM Plex Sans', sans-serif",
    lineHeight: 1.5,
    fontWeight: 400,
  },
  muted: {
    fontSize: 12,
    color: "#666",
    fontFamily: "'IBM Plex Sans', sans-serif",
    lineHeight: 1.5,
    fontWeight: 400,
  },
  meta: {
    fontSize: 10,
    color: "#555",
    fontFamily: "'IBM Plex Sans', sans-serif",
    lineHeight: 1.5,
    fontWeight: 400,
  },
};

function AwaitingInline({ style = {} }) {
  return <span style={{ ...TEXT_STYLES.muted, ...style }}>{AWAITING}</span>;
}

function AwaitingBlock({ style = {} }) {
  return <div style={{ ...TEXT_STYLES.muted, ...style }}>{AWAITING}</div>;
}

function fmtTime(ts) {
  if (!ts) return AWAITING;
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return AWAITING;
  return d.toLocaleTimeString("en-US", { hour12: false });
}

function buildMessageLine(event) {
  if (!event) return "";
  const msg = (event.message_text || "").trim();
  const decision = (event.decision || "").trim();
  if (msg && decision) return `${msg} -> ${decision}`;
  return msg || decision || "";
}

// MICRO COMPONENTS

function Led({ color = "#2ecc40", size = 8, pulse = false, label }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
      <span style={{
        width: size, height: size, borderRadius: "50%", background: color,
        boxShadow: `0 0 ${pulse ? 8 : 4}px ${color}`, display: "inline-block",
        animation: pulse ? "ledPulse 2s ease-in-out infinite" : "none",
      }} />
      {label && <span style={{ fontSize: 10, color: "#8a8a8a", letterSpacing: 1, fontFamily: "'JetBrains Mono', monospace" }}>{label}</span>}
    </span>
  );
}

function RackLabel({ children }) {
  return (
    <div style={{
      fontSize: 9, letterSpacing: 2.5, color: "#5a5a5a", textTransform: "uppercase",
      fontFamily: "'JetBrains Mono', monospace", fontWeight: 600, marginBottom: 6, userSelect: "none",
    }}>{children}</div>
  );
}

function RackPanel({ children, style = {} }) {
  return (
    <div style={{
      background: "#1a1a1e", border: "1px solid #2a2a2e", borderRadius: 3,
      padding: 16, position: "relative", ...style,
    }}>
      <div style={{ position: "absolute", top: 6, left: 8, width: 4, height: 4, borderRadius: "50%", background: "#252528", border: "1px solid #333" }} />
      <div style={{ position: "absolute", top: 6, right: 8, width: 4, height: 4, borderRadius: "50%", background: "#252528", border: "1px solid #333" }} />
      {children}
    </div>
  );
}

function BigButton({ label, color = "#ff4136", onClick, active = false, style = {} }) {
  const [pressed, setPressed] = useState(false);
  return (
    <button onClick={onClick} onMouseDown={() => setPressed(true)} onMouseUp={() => setPressed(false)} onMouseLeave={() => setPressed(false)}
      style={{
        background: active ? color : "#2a2a2e", color: active ? "#fff" : "#888",
        border: `2px solid ${active ? color : "#3a3a3e"}`, borderRadius: 4,
        padding: "14px 28px", fontSize: 13, fontWeight: 700, letterSpacing: 2,
        fontFamily: "'JetBrains Mono', monospace", cursor: "pointer", textTransform: "uppercase",
        transition: "all 0.1s ease", transform: pressed ? "scale(0.97)" : "scale(1)",
        boxShadow: active ? `0 0 20px ${color}44, inset 0 1px 0 ${color}66` : "inset 0 1px 0 #3a3a3e",
        ...style,
      }}>{label}</button>
  );
}

function Toggle({ on, onToggle }) {
  return (
    <button onClick={onToggle} style={{
      display: "flex", alignItems: "center", gap: 10, background: "none",
      border: "1px solid #333", borderRadius: 3, padding: "8px 14px", cursor: "pointer",
      fontFamily: "'JetBrains Mono', monospace",
    }}>
      <div style={{
        width: 36, height: 18, borderRadius: 9, background: on ? "#2ecc40" : "#3a3a3e",
        position: "relative", transition: "background 0.2s", boxShadow: on ? "0 0 8px #2ecc4044" : "none",
      }}>
        <div style={{
          width: 14, height: 14, borderRadius: "50%", background: "#fff",
          position: "absolute", top: 2, left: on ? 20 : 2, transition: "left 0.2s",
        }} />
      </div>
      <span style={{ fontSize: 11, color: on ? "#2ecc40" : "#666", letterSpacing: 1.5, fontWeight: 600 }}>
        {on ? "ACTIVE" : "INACTIVE"}
      </span>
    </button>
  );
}

function MeterBar({ value = 0, max = 100, color = "#2ecc40", label }) {
  const pct = Math.min((value / max) * 100, 100);
  return (
    <div style={{ marginBottom: 8 }}>
      {label && <RackLabel>{label}</RackLabel>}
      <div style={{ height: 6, background: "#252528", borderRadius: 1, overflow: "hidden", border: "1px solid #333" }}>
        <div style={{ height: "100%", width: `${pct}%`, background: `linear-gradient(90deg, ${color}88, ${color})`, borderRadius: 1, transition: "width 0.5s ease" }} />
      </div>
    </div>
  );
}

function Timestamp({ time }) {
  return <span style={{ color: "#555", fontSize: 11, fontFamily: "'JetBrains Mono', monospace", fontVariantNumeric: "tabular-nums" }}>{time}</span>;
}

function RackButton({ label, color = "#7faacc", onClick, disabled = false }) {
  return (
    <button onClick={disabled ? undefined : onClick} style={{
      background: disabled ? "#1a1a1e" : "#2a2a2e", color: disabled ? "#444" : color,
      border: `1px solid ${disabled ? "#2a2a2e" : color + "44"}`, borderRadius: 2,
      padding: "6px 16px", fontSize: 10, fontWeight: 700, letterSpacing: 1.5,
      fontFamily: "'JetBrains Mono', monospace", cursor: disabled ? "not-allowed" : "pointer",
      opacity: disabled ? 0.5 : 1, textTransform: "uppercase",
    }}>{label}</button>
  );
}

function NavButton({ page, activePage, setActivePage }) {
  return (
    <button onClick={() => setActivePage(page.id)} style={{
      display: "flex", alignItems: "center", gap: 8, width: "100%", padding: "10px 16px",
      background: activePage === page.id ? "#1a1a1e" : "transparent", border: "none",
      borderLeft: activePage === page.id ? "2px solid #7faacc" : "2px solid transparent",
      color: activePage === page.id ? "#ccc" : "#555",
      fontSize: 10, letterSpacing: 1.8, fontFamily: "'JetBrains Mono', monospace",
      fontWeight: activePage === page.id ? 700 : 500, cursor: "pointer", textAlign: "left",
      transition: "all 0.15s",
    }}>{page.label}</button>
  );
}

// Tri-level indicator for Cultural Snapshot
function LevelIndicator({ level }) {
  const colors = { low: "#555", moderate: "#ff851b", high: "#2ecc40" };
  const c = colors[level] || "#555";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      {["low", "moderate", "high"].map((l) => (
        <div key={l} style={{
          width: 16, height: 5, borderRadius: 1,
          background: (l === "low" && (level === "low" || level === "moderate" || level === "high")) ||
                     (l === "moderate" && (level === "moderate" || level === "high")) ||
                     (l === "high" && level === "high") ? c : "#252528",
          border: "1px solid #333",
        }} />
      ))}
      <span style={{ fontSize: 10, color: c, letterSpacing: 1.5, fontFamily: "'JetBrains Mono', monospace", fontWeight: 600, textTransform: "uppercase", marginLeft: 4 }}>{level}</span>
    </div>
  );
}

function DashboardAuthOverlay({ visible, busy, errorText, onSubmit }) {
  const [rememberLogin, setRememberLogin] = useState(() => {
    if (typeof window === "undefined") return false;
    return window.localStorage.getItem(REMEMBER_LOGIN_STORAGE_KEY) === "1";
  });
  const [username, setUsername] = useState(() => {
    if (typeof window === "undefined") return "";
    const remember = window.localStorage.getItem(REMEMBER_LOGIN_STORAGE_KEY) === "1";
    if (!remember) return "";
    const saved = window.localStorage.getItem(LAST_LOGIN_USERNAME_STORAGE_KEY);
    return saved ? String(saved) : "";
  });
  const [password, setPassword] = useState("");
  if (!visible) return null;
  return (
    <div style={{
      position: "fixed", inset: 0, background: "rgba(10,10,12,0.86)",
      display: "flex", alignItems: "center", justifyContent: "center", zIndex: 9999,
      backdropFilter: "blur(2px)",
    }}>
      <div style={{
        width: 360, background: "#15151a", border: "1px solid #2a2a2e", borderRadius: 4, padding: 18,
      }}>
        <RackLabel>Dashboard Login</RackLabel>
        <div style={{ fontSize: 11, color: "#666", fontFamily: "'JetBrains Mono', monospace", marginBottom: 10 }}>
          Local session required
        </div>
        <input
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          placeholder="username"
          style={{
            width: "100%", marginBottom: 8, background: "#101015", border: "1px solid #2a2a2e", color: "#ccc",
            borderRadius: 2, padding: "8px 10px", fontSize: 12, fontFamily: "'JetBrains Mono', monospace",
          }}
        />
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="password"
          style={{
            width: "100%", marginBottom: 10, background: "#101015", border: "1px solid #2a2a2e", color: "#ccc",
            borderRadius: 2, padding: "8px 10px", fontSize: 12, fontFamily: "'JetBrains Mono', monospace",
          }}
        />
        {errorText ? <div style={{ fontSize: 11, color: "#ff851b", marginBottom: 8 }}>{errorText}</div> : null}
        <label style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10, ...TEXT_STYLES.muted }}>
          <input
            type="checkbox"
            checked={rememberLogin}
            onChange={(e) => setRememberLogin(Boolean(e.target.checked))}
          />
          Remember login info
        </label>
        <button
          onClick={() => onSubmit(username, password, rememberLogin)}
          disabled={busy || !username || !password}
          style={{
            width: "100%", background: "#2a2a2e", color: "#ccc", border: "1px solid #3a3a3e", borderRadius: 2,
            padding: "8px 10px", cursor: busy ? "not-allowed" : "pointer",
            fontSize: 11, letterSpacing: 1.4, fontFamily: "'JetBrains Mono', monospace", opacity: busy ? 0.6 : 1,
          }}
        >
          {busy ? "SIGNING IN..." : "SIGN IN"}
        </button>
      </div>
    </div>
  );
}

function useDashboardData() {
  const [operatorKey] = useState(OPERATOR_KEY);
  const [operatorActor, setOperatorActor] = useState(() => String(INITIAL_OPERATOR_ACTOR || "").trim());
  const [authData, setAuthData] = useState({ authenticated: false, username: null, role: null });
  const [authChecked, setAuthChecked] = useState(false);
  const [statusData, setStatusData] = useState(EMPTY_STATUS);
  const [eventsData, setEventsData] = useState([]);
  const [suppressionsData, setSuppressionsData] = useState([]);
  const [operatorLogData, setOperatorLogData] = useState([]);
  const [queueData, setQueueData] = useState([]);
  const [studioProfileData, setStudioProfileData] = useState(null);
  const [logsEventsData, setLogsEventsData] = useState([]);
  const [logsSuppressionsData, setLogsSuppressionsData] = useState([]);
  const [logsOperatorData, setLogsOperatorData] = useState([]);
  const [libraryStatusData, setLibraryStatusData] = useState(null);
  const [librarySearchData, setLibrarySearchData] = useState({ q: "", confidence: "NONE", matches: [] });
  const [providersStatusData, setProvidersStatusData] = useState(null);
  const [systemHealthData, setSystemHealthData] = useState(null);
  const [readinessData, setReadinessData] = useState(null);
  const [routingStatusData, setRoutingStatusData] = useState(null);
  const [sensesStatusData, setSensesStatusData] = useState(null);
  const [culturalNotesData, setCulturalNotesData] = useState([]);
  const [viewerNotesData, setViewerNotesData] = useState([]);
  const [memoryPendingData, setMemoryPendingData] = useState([]);
  const [twitchStatusData, setTwitchStatusData] = useState(null);
  const [twitchNotice, setTwitchNotice] = useState("");
  const refreshDataInFlightRef = useRef(false);

  const refreshTwitchStatus = async () => {
    try {
      const twitchStatusRes = await apiFetch(`${API_BASE}/api/twitch/status`);
      if (twitchStatusRes.ok) {
        setTwitchStatusData(await twitchStatusRes.json());
      }
    } catch (_err) {
      // Keep prior Twitch status on failure.
    }
  };

  const refreshData = async () => {
    if (refreshDataInFlightRef.current) {
      return;
    }
    refreshDataInFlightRef.current = true;
    try {
      const [authRes, statusRes, eventsRes, suppressionsRes, operatorRes, queueRes, studioProfileRes, libraryStatusRes, logsEventsRes, logsSuppressionsRes, logsOperatorRes, providersStatusRes, routingStatusRes, sensesStatusRes, memoryCulturalRes, memoryViewersRes, memoryPendingRes, twitchStatusRes] = await Promise.all([
        apiFetch(`${API_BASE}/api/auth/me`),
        apiFetch(`${API_BASE}/api/status`),
        apiFetch(`${API_BASE}/api/events?limit=5`),
        apiFetch(`${API_BASE}/api/suppressions?limit=5`),
        apiFetch(`${API_BASE}/api/operator_log?limit=5`),
        apiFetch(`${API_BASE}/api/queue?limit=25`),
        apiFetch(`${API_BASE}/api/studio_profile`),
        apiFetch(`${API_BASE}/api/library_index/status`),
        apiFetch(`${API_BASE}/api/logs/events?limit=100&offset=0`),
        apiFetch(`${API_BASE}/api/logs/suppressions?limit=100&offset=0`),
        apiFetch(`${API_BASE}/api/logs/operator?limit=100&offset=0`),
        apiFetch(`${API_BASE}/api/providers/status`),
        apiFetch(`${API_BASE}/api/routing/status`),
        apiFetch(`${API_BASE}/api/senses/status`),
        apiFetch(`${API_BASE}/api/memory/cultural?limit=100&offset=0&active_only=0`),
        apiFetch(`${API_BASE}/api/memory/viewers?limit=100&offset=0&active_only=0`),
        apiFetch(`${API_BASE}/api/memory/pending?limit=100&offset=0`),
        apiFetch(`${API_BASE}/api/twitch/status`),
      ]);
      if (authRes.ok) {
        setAuthData(await authRes.json());
      } else {
        setAuthData({ authenticated: false, username: null, role: null });
      }
      setAuthChecked(true);
      if (statusRes.ok) setStatusData(await statusRes.json());
      if (eventsRes.ok) setEventsData(await eventsRes.json());
      if (suppressionsRes.ok) setSuppressionsData(await suppressionsRes.json());
      if (operatorRes.ok) setOperatorLogData(await operatorRes.json());
      if (queueRes.ok) setQueueData(await queueRes.json());
      if (studioProfileRes.ok) setStudioProfileData(await studioProfileRes.json());
      if (libraryStatusRes.ok) setLibraryStatusData(await libraryStatusRes.json());
      if (logsEventsRes.ok) {
        const body = await logsEventsRes.json();
        setLogsEventsData(Array.isArray(body?.items) ? body.items : []);
      }
      if (logsSuppressionsRes.ok) {
        const body = await logsSuppressionsRes.json();
        setLogsSuppressionsData(Array.isArray(body?.items) ? body.items : []);
      }
      if (logsOperatorRes.ok) {
        const body = await logsOperatorRes.json();
        setLogsOperatorData(Array.isArray(body?.items) ? body.items : []);
      }
      if (providersStatusRes.ok) setProvidersStatusData(await providersStatusRes.json());
      if (routingStatusRes.ok) setRoutingStatusData(await routingStatusRes.json());
      if (sensesStatusRes.ok) setSensesStatusData(await sensesStatusRes.json());
      if (memoryCulturalRes.ok) {
        const body = await memoryCulturalRes.json();
        setCulturalNotesData(Array.isArray(body?.items) ? body.items : []);
      }
      if (memoryViewersRes.ok) {
        const body = await memoryViewersRes.json();
        setViewerNotesData(Array.isArray(body?.items) ? body.items : []);
      }
      if (memoryPendingRes.ok) {
        const body = await memoryPendingRes.json();
        setMemoryPendingData(Array.isArray(body?.items) ? body.items : []);
      }
      if (twitchStatusRes.ok) {
        setTwitchStatusData(await twitchStatusRes.json());
      }
    } catch (_err) {
      // Polling errors keep prior data.
      setAuthChecked(true);
    } finally {
      refreshDataInFlightRef.current = false;
    }
  };

  const refreshCoreData = async () => {
    try {
      const [authRes, statusRes, providersStatusRes, twitchStatusRes] = await Promise.all([
        apiFetch(`${API_BASE}/api/auth/me`),
        apiFetch(`${API_BASE}/api/status`),
        apiFetch(`${API_BASE}/api/providers/status`),
        apiFetch(`${API_BASE}/api/twitch/status`),
      ]);
      if (authRes.ok) {
        setAuthData(await authRes.json());
      } else {
        setAuthData({ authenticated: false, username: null, role: null });
      }
      setAuthChecked(true);
      if (statusRes.ok) setStatusData(await statusRes.json());
      if (providersStatusRes.ok) setProvidersStatusData(await providersStatusRes.json());
      if (twitchStatusRes.ok) setTwitchStatusData(await twitchStatusRes.json());
    } catch (_err) {
      setAuthChecked(true);
    }
  };

  const refreshSystemHealth = async () => {
    try {
      const [healthRes, readinessRes] = await Promise.all([
        apiFetch(`${API_BASE}/api/system/health`),
        apiFetch(`${API_BASE}/api/system/readiness`),
      ]);
      if (healthRes.ok) setSystemHealthData(await healthRes.json());
      if (readinessRes.ok) setReadinessData(await readinessRes.json());
    } catch (_err) {
      // Keep prior health on failure.
    }
  };

  useEffect(() => {
    if (!authData?.authenticated) return;
    const currentActor = String(operatorActor || "").trim();
    if (currentActor) return;
    const sessionUser = String(authData?.username || "").trim().toLowerCase();
    if (!sessionUser) return;
    setOperatorActor(sessionUser);
    if (typeof window !== "undefined" && !window.__ROONIE_OPERATOR_ACTOR__) {
      window.localStorage.setItem(OPERATOR_ACTOR_STORAGE_KEY, sessionUser);
    }
  }, [authData?.authenticated, authData?.username, operatorActor]);

  const buildOperatorHeaders = ({ json = true } = {}) => {
    const headers = {};
    if (json) headers["Content-Type"] = "application/json";
    if (operatorKey) headers["X-ROONIE-OP-KEY"] = operatorKey;
    if (operatorActor) {
      headers["X-ROONIE-ACTOR"] = operatorActor;
      headers["X-ROONIE-OP-ACTOR"] = operatorActor;
    }
    return headers;
  };

  const performAction = async (path, payload = {}) => {
    const headers = buildOperatorHeaders({ json: true });
    try {
      const response = await apiFetch(`${API_BASE}${path}`, {
        method: "POST",
        headers,
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        let detail = response.statusText;
        try {
          const body = await response.json();
          detail = body.detail || body.error || detail;
        } catch (_err) {
          // Keep status text fallback.
        }
        console.error(`[Dashboard D1] action failed: ${path} (${response.status}) ${detail}`);
      }
    } catch (err) {
      console.error(`[Dashboard D1] action error: ${path}`, err);
    } finally {
      await refreshData();
    }
  };

  const saveStudioProfile = async (profile, method = "PUT") => {
    const headers = buildOperatorHeaders({ json: true });
    try {
      const response = await apiFetch(`${API_BASE}/api/studio_profile`, {
        method,
        headers,
        body: JSON.stringify(profile || {}),
      });
      if (!response.ok) {
        let detail = response.statusText;
        try {
          const body = await response.json();
          detail = body.detail || body.error || detail;
        } catch (_err) {
          // Keep status text fallback.
        }
        console.error(`[Dashboard D2] studio_profile save failed (${response.status}) ${detail}`);
      } else {
        const body = await response.json();
        if (body && body.profile) setStudioProfileData(body.profile);
      }
    } catch (err) {
      console.error("[Dashboard D2] studio_profile save error", err);
    } finally {
      await refreshData();
    }
  };

  const searchLibraryIndex = async (q, limit = 25) => {
    const query = String(q || "").trim();
    if (!query) {
      setLibrarySearchData({ q: "", confidence: "NONE", matches: [] });
      return;
    }
    try {
      const res = await apiFetch(`${API_BASE}/api/library_index/search?q=${encodeURIComponent(query)}&limit=${limit}`);
      if (res.ok) setLibrarySearchData(await res.json());
    } catch (_err) {
      // Keep prior search data on failure.
    }
  };

  const rebuildLibraryIndex = async () => {
    const headers = buildOperatorHeaders({ json: true });
    try {
      const response = await apiFetch(`${API_BASE}/api/library_index/rebuild`, {
        method: "POST",
        headers,
        body: JSON.stringify({}),
      });
      if (!response.ok) {
        let detail = response.statusText;
        try {
          const body = await response.json();
          detail = body.detail || body.error || detail;
        } catch (_err) {
          // keep status fallback
        }
        console.error(`[Dashboard D3] rebuild failed (${response.status}) ${detail}`);
      }
    } catch (err) {
      console.error("[Dashboard D3] rebuild error", err);
    } finally {
      await refreshData();
    }
  };

  const uploadLibraryXml = async (file) => {
    if (!file) return;
    const headers = buildOperatorHeaders({ json: false });
    try {
      const form = new FormData();
      form.append("file", file, file.name || "rekordbox.xml");
      const uploadRes = await apiFetch(`${API_BASE}/api/library_index/upload_xml`, {
        method: "POST",
        headers,
        body: form,
      });
      if (!uploadRes.ok) {
        let detail = uploadRes.statusText;
        try {
          const body = await uploadRes.json();
          detail = body.detail || body.error || detail;
        } catch (_err) {
          // keep status fallback
        }
        console.error(`[Dashboard D3] upload failed (${uploadRes.status}) ${detail}`);
        return;
      }
      await rebuildLibraryIndex();
    } catch (err) {
      console.error("[Dashboard D3] upload error", err);
    } finally {
      await refreshData();
    }
  };

  const setProviderActive = async (provider) => {
    const headers = buildOperatorHeaders({ json: true });
    try {
      const response = await apiFetch(`${API_BASE}/api/providers/set_active`, {
        method: "POST",
        headers,
        body: JSON.stringify({ provider }),
      });
      if (!response.ok) {
        let detail = response.statusText;
        try {
          const body = await response.json();
          detail = body.detail || body.error || detail;
        } catch (_err) {
          // Keep status text fallback.
        }
        console.error(`[Dashboard D5] set_active failed (${response.status}) ${detail}`);
      }
    } catch (err) {
      console.error("[Dashboard D5] set_active error", err);
    } finally {
      await refreshData();
      await refreshSystemHealth();
    }
  };

  const setProviderCaps = async (capsPatch) => {
    const headers = buildOperatorHeaders({ json: true });
    try {
      const response = await apiFetch(`${API_BASE}/api/providers/caps`, {
        method: "PATCH",
        headers,
        body: JSON.stringify(capsPatch || {}),
      });
      if (!response.ok) {
        let detail = response.statusText;
        try {
          const body = await response.json();
          detail = body.detail || body.error || detail;
        } catch (_err) {
          // Keep status text fallback.
        }
        console.error(`[Dashboard D5] set_caps failed (${response.status}) ${detail}`);
      }
    } catch (err) {
      console.error("[Dashboard D5] set_caps error", err);
    } finally {
      await refreshData();
      await refreshSystemHealth();
    }
  };

  const setRoutingEnabled = async (enabled) => {
    const headers = buildOperatorHeaders({ json: true });
    try {
      const response = await apiFetch(`${API_BASE}/control/routing`, {
        method: "POST",
        headers,
        body: JSON.stringify({ enabled: Boolean(enabled) }),
      });
      if (!response.ok) {
        let detail = response.statusText;
        try {
          const body = await response.json();
          detail = body.detail || body.error || detail;
        } catch (_err) {
          // Keep status text fallback.
        }
        console.error(`[Dashboard P16] routing toggle failed (${response.status}) ${detail}`);
      }
    } catch (err) {
      console.error("[Dashboard P16] routing toggle error", err);
    } finally {
      await refreshData();
      await refreshSystemHealth();
    }
  };

  const setActiveDirector = async (active) => {
    const headers = buildOperatorHeaders({ json: true });
    try {
      const response = await apiFetch(`${API_BASE}/control/director`, {
        method: "POST",
        headers,
        body: JSON.stringify({ active }),
      });
      if (!response.ok) {
        let detail = response.statusText;
        try {
          const body = await response.json();
          detail = body.detail || body.error || detail;
        } catch (_err) {
          // Keep status text fallback.
        }
        console.error(`[Dashboard P16] director set failed (${response.status}) ${detail}`);
      }
    } catch (err) {
      console.error("[Dashboard P16] director set error", err);
    } finally {
      await refreshData();
    }
  };

  const setDryRunEnabled = async (enabled) => {
    const headers = buildOperatorHeaders({ json: true });
    try {
      const response = await apiFetch(`${API_BASE}/control/dry_run`, {
        method: "POST",
        headers,
        body: JSON.stringify({ enabled: Boolean(enabled) }),
      });
      if (!response.ok) {
        let detail = response.statusText;
        try {
          const body = await response.json();
          detail = body.detail || body.error || detail;
        } catch (_err) {
          // Keep status text fallback.
        }
        console.error(`[Dashboard P21] dry_run toggle failed (${response.status}) ${detail}`);
      }
    } catch (err) {
      console.error("[Dashboard P21] dry_run toggle error", err);
    } finally {
      await refreshData();
    }
  };

  const saveCulturalNote = async (note, opts = {}) => {
    const method = (opts.method || "POST").toUpperCase();
    const noteId = opts.id || "";
    const headers = buildOperatorHeaders({ json: true });
    const payload = { note, tags: Array.isArray(opts.tags) ? opts.tags : [] };
    const url = method === "POST"
      ? `${API_BASE}/api/memory/cultural`
      : `${API_BASE}/api/memory/cultural/${encodeURIComponent(String(noteId))}`;
    try {
      const response = await apiFetch(url, {
        method,
        headers,
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        let detail = response.statusText;
        try {
          const body = await response.json();
          detail = body.detail || body.error || detail;
        } catch (_err) {
          // keep status fallback
        }
        console.error(`[Dashboard D7] cultural note save failed (${response.status}) ${detail}`);
      }
    } catch (err) {
      console.error("[Dashboard D7] cultural note save error", err);
    } finally {
      await refreshData();
    }
  };

  const deleteCulturalNote = async (id) => {
    const noteId = String(id || "").trim();
    if (!noteId) return;
    const headers = buildOperatorHeaders({ json: false });
    try {
      const response = await apiFetch(`${API_BASE}/api/memory/cultural/${encodeURIComponent(noteId)}`, {
        method: "DELETE",
        headers,
      });
      if (!response.ok) {
        let detail = response.statusText;
        try {
          const body = await response.json();
          detail = body.detail || body.error || detail;
        } catch (_err) {
          // keep status fallback
        }
        console.error(`[Dashboard D7] cultural note delete failed (${response.status}) ${detail}`);
      }
    } catch (err) {
      console.error("[Dashboard D7] cultural note delete error", err);
    } finally {
      await refreshData();
    }
  };

  const saveViewerNote = async (payload, opts = {}) => {
    const method = (opts.method || "POST").toUpperCase();
    const noteId = opts.id || "";
    const headers = buildOperatorHeaders({ json: true });
    const bodyPayload = method === "POST"
      ? {
          viewer_handle: payload?.viewer_handle,
          note: payload?.note,
          tags: Array.isArray(payload?.tags) ? payload.tags : [],
        }
      : {
          note: payload?.note,
          tags: Array.isArray(payload?.tags) ? payload.tags : undefined,
          is_active: payload?.is_active,
        };
    if (method !== "POST") {
      if (!("note" in bodyPayload)) delete bodyPayload.note;
      if (!("tags" in bodyPayload)) delete bodyPayload.tags;
      if (!("is_active" in bodyPayload)) delete bodyPayload.is_active;
    }
    const url = method === "POST"
      ? `${API_BASE}/api/memory/viewer`
      : `${API_BASE}/api/memory/viewer/${encodeURIComponent(String(noteId))}`;
    try {
      const response = await apiFetch(url, {
        method,
        headers,
        body: JSON.stringify(bodyPayload),
      });
      if (!response.ok) {
        let detail = response.statusText;
        try {
          const body = await response.json();
          detail = body.detail || body.error || detail;
        } catch (_err) {
          // keep status fallback
        }
        console.error(`[Dashboard D7] viewer note save failed (${response.status}) ${detail}`);
      }
    } catch (err) {
      console.error("[Dashboard D7] viewer note save error", err);
    } finally {
      await refreshData();
    }
  };

  const deleteViewerNote = async (id) => {
    const noteId = String(id || "").trim();
    if (!noteId) return;
    const headers = buildOperatorHeaders({ json: false });
    try {
      const response = await apiFetch(`${API_BASE}/api/memory/viewer/${encodeURIComponent(noteId)}`, {
        method: "DELETE",
        headers,
      });
      if (!response.ok) {
        let detail = response.statusText;
        try {
          const body = await response.json();
          detail = body.detail || body.error || detail;
        } catch (_err) {
          // keep status fallback
        }
        console.error(`[Dashboard D7] viewer note delete failed (${response.status}) ${detail}`);
      }
    } catch (err) {
      console.error("[Dashboard D7] viewer note delete error", err);
    } finally {
      await refreshData();
    }
  };

  const reviewMemoryPending = async (id, decision, reason = "") => {
    const candidateId = String(id || "").trim();
    const action = String(decision || "").trim().toLowerCase();
    if (!candidateId || !["approve", "deny"].includes(action)) return;
    const headers = buildOperatorHeaders({ json: true });
    const endpoint = `${API_BASE}/api/memory/pending/${encodeURIComponent(candidateId)}/${action}`;
    const payload = action === "deny" ? { reason: String(reason || "").trim() } : {};
    try {
      const response = await apiFetch(endpoint, {
        method: "POST",
        headers,
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        let detail = response.statusText;
        try {
          const body = await response.json();
          detail = body.detail || body.error || detail;
        } catch (_err) {
          // keep status fallback
        }
        console.error(`[Dashboard D7] memory pending ${action} failed (${response.status}) ${detail}`);
      }
    } catch (err) {
      console.error(`[Dashboard D7] memory pending ${action} error`, err);
    } finally {
      await refreshData();
    }
  };

  const loginDashboard = async (username, password, rememberLogin = false) => {
    try {
      const response = await apiFetch(`${API_BASE}/api/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      const body = await response.json();
      if (response.ok && body?.authenticated) {
        setAuthData(body);
        if (typeof window !== "undefined") {
          if (rememberLogin) {
            window.localStorage.setItem(REMEMBER_LOGIN_STORAGE_KEY, "1");
            window.localStorage.setItem(LAST_LOGIN_USERNAME_STORAGE_KEY, String(username || "").trim());
          } else {
            window.localStorage.removeItem(REMEMBER_LOGIN_STORAGE_KEY);
            window.localStorage.removeItem(LAST_LOGIN_USERNAME_STORAGE_KEY);
          }
        }
        await refreshData();
        return { ok: true, body };
      }
      return { ok: false, body };
    } catch (err) {
      return { ok: false, body: { detail: String(err || "login error") } };
    }
  };

  const logoutDashboard = async () => {
    try {
      await apiFetch(`${API_BASE}/api/auth/logout`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
    } catch (_err) {
      // Ignore logout errors; refresh handles state.
    } finally {
      if (typeof window !== "undefined") {
        window.localStorage.removeItem(LAST_LOGIN_USERNAME_STORAGE_KEY);
        window.localStorage.removeItem(REMEMBER_LOGIN_STORAGE_KEY);
      }
      setAuthData({ authenticated: false, username: null, role: null });
      setAuthChecked(true);
      await refreshData();
    }
  };

  const twitchConnectStart = async (account) => {
    const headers = buildOperatorHeaders({ json: true });
    const payload = { account: String(account || "bot").toLowerCase() };
    try {
      const response = await apiFetch(`${API_BASE}/api/twitch/connect_start`, {
        method: "POST",
        headers,
        body: JSON.stringify(payload),
      });
      let body = {};
      try {
        body = await response.json();
      } catch (_err) {
        body = {};
      }
      const success = Boolean(response.ok && body?.ok !== false);
      if (!success) {
        setTwitchNotice(String(body?.detail || body?.error || "Unable to start Twitch auth."));
      }
      await refreshTwitchStatus();
      return { ok: success, body };
    } catch (err) {
      setTwitchNotice(String(err || "Unable to start Twitch auth."));
      await refreshTwitchStatus();
      return { ok: false, body: { detail: String(err || "connect error") } };
    }
  };

  const twitchDisconnect = async (account) => {
    const headers = buildOperatorHeaders({ json: true });
    const payload = { account: String(account || "bot").toLowerCase() };
    try {
      const response = await apiFetch(`${API_BASE}/api/twitch/disconnect`, {
        method: "POST",
        headers,
        body: JSON.stringify(payload),
      });
      let body = {};
      try {
        body = await response.json();
      } catch (_err) {
        body = {};
      }
      const success = Boolean(response.ok && body?.ok !== false);
      if (!success) {
        setTwitchNotice(String(body?.detail || body?.error || "Unable to disconnect account."));
      } else {
        setTwitchNotice("Account disconnected.");
      }
      await refreshTwitchStatus();
      return { ok: success, body };
    } catch (err) {
      setTwitchNotice(String(err || "Unable to disconnect account."));
      await refreshTwitchStatus();
      return { ok: false, body: { detail: String(err || "disconnect error") } };
    }
  };

  useEffect(() => {
    refreshData();
    refreshSystemHealth();
    const coreInterval = setInterval(refreshCoreData, 2500);
    const fullInterval = setInterval(refreshData, 15000);
    const healthInterval = setInterval(refreshSystemHealth, 8000);
    return () => {
      clearInterval(coreInterval);
      clearInterval(fullInterval);
      clearInterval(healthInterval);
    };
  }, []);

  useEffect(() => {
    const onMessage = async (event) => {
      const data = event?.data || {};
      if (data?.type !== "ROONIE_TWITCH_AUTH_COMPLETE") return;
      await refreshTwitchStatus();
      const ok = Boolean(data?.ok);
      const account = String(data?.account || "account");
      setTwitchNotice(ok ? `${account} connected.` : `${account} connection failed.`);
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  return {
    authData,
    authChecked,
    statusData,
    eventsData,
    suppressionsData,
    operatorLogData,
    queueData,
    studioProfileData,
    logsEventsData,
    logsSuppressionsData,
    logsOperatorData,
    libraryStatusData,
    librarySearchData,
    providersStatusData,
    systemHealthData,
    readinessData,
    routingStatusData,
    sensesStatusData,
    culturalNotesData,
    viewerNotesData,
    memoryPendingData,
    twitchStatusData,
    twitchNotice,
    setTwitchNotice,
    performAction,
    saveStudioProfile,
    searchLibraryIndex,
    uploadLibraryXml,
    rebuildLibraryIndex,
    setProviderActive,
    setProviderCaps,
    setRoutingEnabled,
    setActiveDirector,
    setDryRunEnabled,
    saveCulturalNote,
    deleteCulturalNote,
    saveViewerNote,
    deleteViewerNote,
    reviewMemoryPending,
    loginDashboard,
    logoutDashboard,
    twitchConnectStart,
    twitchDisconnect,
  };
}

// --- PAGE: LIVE ---

function LivePage({ statusData, eventsData, suppressionsData, performAction }) {
  const status = statusData.silenced ? "SILENCED" : (statusData.armed ? "ACTIVE" : "INACTIVE");
  const autoNext = eventsData.length ? buildMessageLine(eventsData[0]) : AWAITING;
  const suppression = suppressionsData.length ? suppressionsData[0] : null;
  const silenceUntilMs = statusData.silence_until ? Date.parse(statusData.silence_until) : NaN;
  const silenceTimer = Number.isFinite(silenceUntilMs)
    ? Math.max(0, Math.ceil((silenceUntilMs - Date.now()) / 1000))
    : null;
  const chatActivity = eventsData.length >= 5 ? "BUSY" : (eventsData.length >= 2 ? "FLOWING" : "QUIET");
  const contextText = statusData.context_last_active
    ? `${statusData.context_last_turns_used}-turn carry active`
    : AWAITING;
  const activityColors = { QUIET: "#555", FLOWING: "#2ecc40", BUSY: "#ff851b" };
  const handleSilence = () => performAction("/api/live/silence_now", { ttl_seconds: 300 });
  const handleToggle = () => performAction(statusData.armed ? "/api/live/disarm" : "/api/live/arm", {});

  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, gridTemplateRows: "auto auto 1fr" }}>
      <div style={{
        gridColumn: "1 / -1", background: `${STATUS_COLORS[status]}08`,
        border: `2px solid ${STATUS_COLORS[status]}`, borderRadius: 4, padding: "20px 28px",
        display: "flex", alignItems: "center", justifyContent: "space-between",
        boxShadow: `0 0 30px ${STATUS_COLORS[status]}15, inset 0 0 60px ${STATUS_COLORS[status]}05`,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <Led color={STATUS_COLORS[status]} size={14} pulse={status === "ACTIVE"} />
          <div>
            <div style={{ fontSize: 28, fontWeight: 800, color: STATUS_COLORS[status], letterSpacing: 6, fontFamily: "'JetBrains Mono', monospace", lineHeight: 1 }}>{status}</div>
            <div style={{ fontSize: 10, color: "#666", letterSpacing: 2, marginTop: 4, fontFamily: "'JetBrains Mono', monospace" }}>
              ROONIE STATUS{silenceTimer ? ` - RESUMES IN ${silenceTimer}s` : ""}
            </div>
          </div>
        </div>
        <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
          <BigButton label="SILENCE NOW" color="#ff4136" active={status === "SILENCED"} onClick={handleSilence} style={{ padding: "16px 32px", fontSize: 14 }} />
          <Toggle on={statusData.armed} onToggle={handleToggle} />
        </div>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <RackPanel>
          <RackLabel>Next (Auto-Approved)</RackLabel>
          {status === "ACTIVE" ? (
            <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
              <Led color="#2ecc40" size={6} pulse />
              <div style={{ fontSize: 13, color: "#ccc", lineHeight: 1.5, fontFamily: "'IBM Plex Sans', sans-serif" }}>{autoNext}</div>
            </div>
          ) : (
            <AwaitingBlock />
          )}
          <div style={{ ...TEXT_STYLES.meta, marginTop: 10 }}>
            READ-ONLY - auto-approved messages fire on timing rules - Context: {contextText}
          </div>
        </RackPanel>

        <RackPanel>
          <RackLabel>Why Blocked - Last Suppression</RackLabel>
          <div style={{ display: "flex", alignItems: "flex-start", gap: 10, marginBottom: 8 }}>
            <Led color="#ff851b" size={6} />
            <div>
              <div style={{ fontSize: 12, color: "#ccc", fontFamily: "'IBM Plex Sans', sans-serif", lineHeight: 1.5 }}>
                {suppression ? `${suppression.suppression_reason || "SUPPRESSED"}${suppression.suppression_detail ? ` (${suppression.suppression_detail})` : ""}` : AWAITING}
              </div>
              <div style={{ ...TEXT_STYLES.meta, marginTop: 4 }}>
                RULE: {suppression ? (suppression.suppression_reason || "UNKNOWN") : AWAITING} - {suppression ? fmtTime(suppression.ts) : AWAITING}
              </div>
            </div>
          </div>
        </RackPanel>

        <RackPanel>
          <RackLabel>Chat Activity</RackLabel>
          <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
            {["QUIET", "FLOWING", "BUSY"].map((level) => (
              <div key={level} style={{
                display: "flex", alignItems: "center", gap: 6, padding: "4px 10px", borderRadius: 2,
                background: chatActivity === level ? `${activityColors[level]}15` : "transparent",
                border: `1px solid ${chatActivity === level ? activityColors[level] : "transparent"}`,
              }}>
                <Led color={activityColors[level]} size={6} pulse={chatActivity === level} />
                <span style={{ fontSize: 11, color: chatActivity === level ? activityColors[level] : "#444", fontWeight: chatActivity === level ? 700 : 400, letterSpacing: 1.5, fontFamily: "'JetBrains Mono', monospace" }}>{level}</span>
              </div>
            ))}
          </div>
        </RackPanel>
      </div>

      <RackPanel style={{ display: "flex", flexDirection: "column" }}>
        <RackLabel>Last 5 Messages - Roonie</RackLabel>
        <div style={{ flex: 1, display: "flex", flexDirection: "column" }}>
          {(eventsData.length ? eventsData : []).map((msg, i) => (
            <div key={i} style={{ padding: "10px 0", borderBottom: i < eventsData.length - 1 ? "1px solid #222" : "none", display: "flex", gap: 12, alignItems: "flex-start" }}>
              <Timestamp time={fmtTime(msg.ts)} />
              <div style={{ fontSize: 12, color: "#aaa", lineHeight: 1.5, fontFamily: "'IBM Plex Sans', sans-serif" }}>{buildMessageLine(msg)}</div>
            </div>
          ))}
          {!eventsData.length && <AwaitingBlock style={{ padding: "10px 0" }} />}
        </div>
      </RackPanel>
    </div>
  );
}
function StudioProfilePage({ studioProfileData, saveStudioProfile }) {
  const [draft, setDraft] = useState(null);

  useEffect(() => {
    if (!studioProfileData) return;
    try {
      setDraft(JSON.parse(JSON.stringify(studioProfileData)));
    } catch (_err) {
      setDraft(studioProfileData);
    }
  }, [studioProfileData]);

  const profile = draft || studioProfileData || {
    location: { display: "" },
    social_links: [],
    gear: [],
    faq: [],
    approved_emotes: [],
  };
  const gearItems = Array.isArray(profile.gear)
    ? profile.gear.map((item) => String(item || "").trim()).filter(Boolean)
    : [];

  const updateDraft = (next) => {
    setDraft(next);
  };

  const editLocation = () => {
    const current = profile.location?.display || "";
    const next = window.prompt("Safe location (general only):", current);
    if (next === null) return;
    const trimmed = next.trim();
    if (!trimmed) return;
    updateDraft({ ...profile, location: { display: trimmed } });
  };

  const editGearItem = (idx) => {
    const current = gearItems[idx] || "";
    const next = window.prompt("Gear item:", current);
    if (next === null) return;
    const trimmed = next.trim();
    const rows = [...gearItems];
    if (!trimmed) {
      rows.splice(idx, 1);
    } else {
      rows[idx] = trimmed;
    }
    updateDraft({ ...profile, gear: rows });
  };

  const addGearItem = () => {
    const item = (window.prompt("Add gear item:", "") || "").trim();
    if (!item) return;
    updateDraft({ ...profile, gear: [...gearItems, item] });
  };

  const editSocialLink = (idx) => {
    const row = (profile.social_links || [])[idx] || {};
    const input = window.prompt("Social link as label|url:", `${row.label || ""}|${row.url || ""}`);
    if (input === null) return;
    const parts = input.split("|");
    if (parts.length < 2) return;
    const label = parts[0].trim();
    const url = parts.slice(1).join("|").trim();
    if (!label || !url) return;
    const rows = [...(profile.social_links || [])];
    rows[idx] = { label, url };
    updateDraft({ ...profile, social_links: rows });
  };

  const addSocialLink = () => {
    const input = window.prompt("Add social link as label|url:", "");
    if (input === null) return;
    const parts = input.split("|");
    if (parts.length < 2) return;
    const label = parts[0].trim();
    const url = parts.slice(1).join("|").trim();
    if (!label || !url) return;
    updateDraft({ ...profile, social_links: [...(profile.social_links || []), { label, url }] });
  };

  const editFaq = (idx) => {
    const row = (profile.faq || [])[idx] || {};
    const qInput = window.prompt("FAQ question:", String(row.q || ""));
    if (qInput === null) return;
    const aInput = window.prompt("FAQ answer:", String(row.a || ""));
    if (aInput === null) return;
    const q = qInput.trim();
    const a = aInput.trim();
    if (!q || !a) return;
    const rows = [...(profile.faq || [])];
    rows[idx] = { q, a };
    updateDraft({ ...profile, faq: rows });
  };

  const addFaq = () => {
    const qInput = window.prompt("Add FAQ question:", "");
    if (qInput === null) return;
    let q = qInput.trim();
    let a = "";
    if (q.includes("|")) {
      const parts = q.split("|");
      q = parts[0].trim();
      a = parts.slice(1).join("|").trim();
    } else {
      const aInput = window.prompt("Add FAQ answer:", "");
      if (aInput === null) return;
      a = aInput.trim();
    }
    if (!q || !a) return;
    updateDraft({ ...profile, faq: [...(profile.faq || []), { q, a }] });
  };

  const editEmotes = () => {
    const current = (profile.approved_emotes || []).join(", ");
    const input = window.prompt("Approved emotes (comma-separated):", current);
    if (input === null) return;
    const next = input.split(",").map((s) => s.trim()).filter(Boolean);
    updateDraft({ ...profile, approved_emotes: next });
  };

  const saveProfile = () => {
    if (!draft) return;
    saveStudioProfile(draft, "PUT");
  };

  return (
    <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 12 }}>
      <RackPanel>
        <RackLabel>Studio Gear List</RackLabel>
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {gearItems.map((item, i) => (
            <div key={i} onClick={() => editGearItem(i)} style={{ padding: "6px 10px", fontSize: 12, color: "#aaa", fontFamily: "'IBM Plex Sans', sans-serif", borderLeft: "2px solid #2a2a2e", marginBottom: 2, cursor: "pointer" }}>
              {item}
            </div>
          ))}
          {!gearItems.length ? <AwaitingBlock style={{ padding: "6px 10px" }} /> : null}
          <div onClick={addGearItem} style={{ fontSize: 10, color: "#666", letterSpacing: 2, fontFamily: "'JetBrains Mono', monospace", marginBottom: 6, fontWeight: 700, cursor: "pointer" }}>+ ADD GEAR ITEM</div>
        </div>
        <button onClick={saveProfile} style={{ marginTop: 16, background: "transparent", border: "1px dashed #333", color: "#555", padding: "8px 16px", fontSize: 10, letterSpacing: 1.5, fontFamily: "'JetBrains Mono', monospace", cursor: "pointer", borderRadius: 2, width: "100%" }}>SAVE STUDIO PROFILE</button>
      </RackPanel>
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <RackPanel><RackLabel>Safe Location</RackLabel><div onClick={editLocation} style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}><Led color="#2ecc40" size={6} /><span style={{ fontSize: 13, color: "#ccc", fontFamily: "'IBM Plex Sans', sans-serif" }}>{profile.location?.display || AWAITING}</span></div><div style={{ fontSize: 10, color: "#444", fontFamily: "'JetBrains Mono', monospace", marginTop: 6 }}>LOCKED - Roonie will only reference this location</div></RackPanel>
        <RackPanel><RackLabel>Social Links</RackLabel>{(profile.social_links || []).map((link, i) => (<div key={i} onClick={() => editSocialLink(i)} style={{ padding: "6px 0", fontSize: 12, color: "#7faacc", fontFamily: "'JetBrains Mono', monospace", borderBottom: "1px solid #222", cursor: "pointer" }}>{link.label}: {link.url}</div>))}{!(profile.social_links || []).length ? <div style={{ padding: "6px 0", fontSize: 11, color: "#666", fontFamily: "'IBM Plex Sans', sans-serif" }}>{AWAITING}</div> : null}<div onClick={addSocialLink} style={{ padding: "6px 0", fontSize: 10, color: "#555", fontFamily: "'JetBrains Mono', monospace", cursor: "pointer" }}>+ add social link</div><div onClick={editEmotes} style={{ padding: "6px 0", fontSize: 11, color: "#888", fontFamily: "'IBM Plex Sans', sans-serif", cursor: "pointer" }}>Approved emotes: {(profile.approved_emotes || []).join(", ") || AWAITING}</div></RackPanel>
        <RackPanel><RackLabel>FAQ Short Answers</RackLabel>{(profile.faq || []).map((faq, i) => (<div key={i} onClick={() => editFaq(i)} style={{ marginBottom: 10, cursor: "pointer" }}><div style={{ fontSize: 11, color: "#888", fontFamily: "'IBM Plex Sans', sans-serif", fontWeight: 600 }}>{faq.q}</div><div style={{ fontSize: 12, color: "#aaa", fontFamily: "'IBM Plex Sans', sans-serif", marginTop: 2, paddingLeft: 8, borderLeft: "2px solid #333" }}>{faq.a}</div></div>))}{!(profile.faq || []).length ? <div style={{ padding: "6px 0", fontSize: 11, color: "#666", fontFamily: "'IBM Plex Sans', sans-serif" }}>{AWAITING}</div> : null}<div onClick={addFaq} style={{ padding: "6px 0", fontSize: 10, color: "#555", fontFamily: "'JetBrains Mono', monospace", cursor: "pointer" }}>+ add faq</div></RackPanel>
      </div>
    </div>
  );
}

// --- PAGE: LIBRARY INDEX ---

function LibraryIndexPage({ libraryStatusData, librarySearchData, searchLibraryIndex, uploadLibraryXml }) {
  const [sq, setSq] = useState("");
  const [uploadLabel, setUploadLabel] = useState("or click to browse");
  const f = sq ? (librarySearchData?.matches || []) : [];
  const hasStatus = libraryStatusData && typeof libraryStatusData === "object";
  const trackCountText = hasStatus && Number.isFinite(Number(libraryStatusData?.track_count))
    ? String(Number(libraryStatusData.track_count))
    : AWAITING;
  const lastUpdatedText = libraryStatusData?.last_indexed_at
    ? new Date(libraryStatusData.last_indexed_at).toLocaleDateString("en-US")
    : AWAITING;

  useEffect(() => {
    const timeout = setTimeout(() => {
      searchLibraryIndex(sq, 25);
    }, 180);
    return () => clearTimeout(timeout);
  }, [sq]);

  const handleFile = async (file) => {
    if (!file) return;
    setUploadLabel(file.name || "uploading...");
    await uploadLibraryXml(file);
    setUploadLabel(file.name || "upload complete");
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        <RackPanel>
          <RackLabel>Rekordbox XML Source</RackLabel>
          <div onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); const file = e.dataTransfer?.files?.[0]; if (file) handleFile(file); }} onClick={() => { const el = document.getElementById("library-xml-picker"); if (el) el.click(); }} style={{ border: "2px dashed #333", borderRadius: 3, padding: 24, textAlign: "center", cursor: "pointer" }}>
            <div style={{ fontSize: 24, color: "#333", marginBottom: 8 }}>^</div>
            <div style={{ fontSize: 11, color: "#555", fontFamily: "'JetBrains Mono', monospace", letterSpacing: 1 }}>DROP REKORDBOX XML HERE</div>
            <div style={{ fontSize: 10, color: "#444", marginTop: 4, fontFamily: "'IBM Plex Sans', sans-serif" }}>{uploadLabel || AWAITING}</div>
            <input id="library-xml-picker" type="file" accept=".xml,text/xml,application/xml" onChange={(e) => handleFile(e.target.files && e.target.files[0])} style={{ display: "none" }} />
          </div>
        </RackPanel>
        <RackPanel>
          <RackLabel>Library Status</RackLabel>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginTop: 8 }}>
            <div>
              <div style={{ fontSize: 32, fontWeight: 800, color: "#ccc", fontFamily: "'JetBrains Mono', monospace", lineHeight: 1 }}>{trackCountText}</div>
              <div style={{ fontSize: 10, color: "#555", letterSpacing: 1.5, fontFamily: "'JetBrains Mono', monospace", marginTop: 4 }}>TOTAL TRACKS</div>
            </div>
            <div>
              <div style={{ fontSize: 14, color: "#888", fontFamily: "'JetBrains Mono', monospace" }}>{lastUpdatedText}</div>
              <div style={{ fontSize: 10, color: "#555", letterSpacing: 1.5, fontFamily: "'JetBrains Mono', monospace", marginTop: 4 }}>LAST UPDATED</div>
            </div>
          </div>
        </RackPanel>
      </div>
      <RackPanel>
        <RackLabel>Track Search</RackLabel>
        <input type="text" value={sq} onChange={(e) => setSq(e.target.value)} placeholder="Search artist, title..." style={{ width: "100%", background: "#111114", border: "1px solid #333", borderRadius: 2, padding: "10px 14px", color: "#ccc", fontSize: 13, fontFamily: "'JetBrains Mono', monospace", outline: "none", boxSizing: "border-box", marginBottom: 12 }} />
        <div style={{ fontSize: 10, color: "#555", letterSpacing: 1.5, fontFamily: "'JetBrains Mono', monospace", display: "grid", gridTemplateColumns: "2fr 3fr 60px 50px", gap: 8, padding: "0 4px", marginBottom: 6 }}><span>ARTIST</span><span>TITLE</span><span>BPM</span><span>KEY</span></div>
        {f.map((t, i) => (
          <div key={i} style={{ display: "grid", gridTemplateColumns: "2fr 3fr 60px 50px", gap: 8, padding: "8px 4px", borderTop: "1px solid #1f1f22", alignItems: "center" }}>
            <span style={{ fontSize: 12, color: "#aaa", fontFamily: "'IBM Plex Sans', sans-serif" }}>{t.artist || AWAITING}</span>
            <span style={{ fontSize: 12, color: "#ccc", fontFamily: "'IBM Plex Sans', sans-serif" }}>{t.title || AWAITING}</span>
            <span style={{ fontSize: 11, color: "#666", fontFamily: "'JetBrains Mono', monospace" }}>{t.bpm || <AwaitingInline />}</span>
            <span style={{ fontSize: 11, color: "#7faacc", fontFamily: "'JetBrains Mono', monospace" }}>{t.key || <AwaitingInline />}</span>
          </div>
        ))}
        {sq && f.length === 0 && <AwaitingBlock style={{ padding: "8px 4px", borderTop: "1px solid #1f1f22" }} />}
      </RackPanel>
    </div>
  );
}

// --- PAGE: ANNOUNCEMENTS & EVENTS ---

function AnnouncementsPage({ queueData, performAction }) {
  const firstQueued = queueData && queueData.length ? queueData[0] : null;
  const queueId = firstQueued && firstQueued.id ? firstQueued.id : null;
  const operatorMsg = firstQueued
    ? (firstQueued.message || firstQueued.text || firstQueued.content || JSON.stringify(firstQueued))
    : null;
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
      <RackPanel>
        <RackLabel>Operator Queue</RackLabel>
        <div style={{ fontSize: 10, color: "#555", fontFamily: "'IBM Plex Sans', sans-serif", marginBottom: 10 }}>Used only for operator-initiated announcements. One message at a time. Manual timing only.</div>
        {operatorMsg ? (<><div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}><Led color="#ff851b" size={6} /><div style={{ fontSize: 13, color: "#ccc", lineHeight: 1.5, fontFamily: "'IBM Plex Sans', sans-serif" }}>"{operatorMsg}"</div></div><div style={{ marginTop: 10, display: "flex", gap: 8 }}><RackButton label="NOT AVAILABLE" color="#7faacc" disabled /><button onClick={() => { if (queueId) performAction("/api/queue/cancel", { id: queueId }); }} style={{ background: "transparent", color: "#ff4136", border: "1px solid #ff413644", borderRadius: 2, padding: "5px 14px", fontSize: 10, fontWeight: 700, letterSpacing: 1.5, cursor: "pointer", fontFamily: "'JetBrains Mono', monospace" }}>CANCEL</button></div></>) : (<AwaitingBlock />)}
        <div style={{ fontSize: 9, color: "#444", fontFamily: "'JetBrains Mono', monospace", marginTop: 10, letterSpacing: 1 }}>MANUAL ONLY - never auto-fires - no timed automation</div>
      </RackPanel>
      <RackPanel>
        <RackLabel>Upcoming Events</RackLabel>
        <div style={{ fontSize: 10, color: "#555", fontFamily: "'IBM Plex Sans', sans-serif", marginBottom: 12 }}>Read-only schedule. Roonie can reference these if asked.</div>
        <div style={{ fontSize: 12, color: "#666", fontFamily: "'IBM Plex Sans', sans-serif" }}>{AWAITING}</div>
      </RackPanel>
    </div>
  );
}

// --- PAGE: NOW PLAYING ---

function NowPlayingPage() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        <RackPanel><RackLabel>File Status</RackLabel><div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}><Led color="#ff851b" size={10} pulse={false} /><AwaitingInline style={{ color: "#ff851b", fontWeight: 700 }} /></div><RackLabel>Last Updated</RackLabel><AwaitingBlock /><div style={{ marginTop: 12 }}><RackButton label="NOT AVAILABLE" color="#7faacc" disabled /></div></RackPanel>
        <RackPanel><RackLabel>File Source</RackLabel><div style={{ padding: "10px 14px", background: "#111114", border: "1px solid #2a2a2e", borderRadius: 2, wordBreak: "break-all" }}><AwaitingInline /></div><div style={{ marginTop: 8, fontSize: 10, color: "#555", fontFamily: "'IBM Plex Sans', sans-serif", lineHeight: 1.6 }}>Read-only track source.</div></RackPanel>
      </div>
      <div style={{ background: "#1a1a1e", border: "1px solid #2a2a2e", borderRadius: 3, padding: 16, position: "relative", opacity: 0.35, pointerEvents: "none", userSelect: "none" }}>
        <div style={{ position: "absolute", top: 6, left: 8, width: 4, height: 4, borderRadius: "50%", background: "#252528", border: "1px solid #333" }} /><div style={{ position: "absolute", top: 6, right: 8, width: 4, height: 4, borderRadius: "50%", background: "#252528", border: "1px solid #333" }} />
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}><RackLabel>API Source (Later)</RackLabel><span style={{ fontSize: 9, padding: "3px 8px", background: "#ff413612", border: "1px solid #ff413633", borderRadius: 2, color: "#ff4136", letterSpacing: 1.5, fontFamily: "'JetBrains Mono', monospace", fontWeight: 700 }}>OFF</span></div>
        <AwaitingBlock style={{ marginBottom: 8 }} />
        <div style={{ borderTop: "1px solid #222", paddingTop: 8 }}><div style={{ fontSize: 9, color: "#444", letterSpacing: 1.5, fontFamily: "'JetBrains Mono', monospace" }}>NOT AVAILABLE</div></div>
      </div>
    </div>
  );
}

// --- PAGE: LOGS & REVIEW ---

function LogsPage({ eventsData, suppressionsData, operatorLogData }) {
  const [at, setAt] = useState("messages");
  const tabs = [{ id: "messages", label: "MESSAGES" }, { id: "suppression", label: "SUPPRESSED" }, { id: "operator", label: "OPERATOR" }];
  const messages = eventsData.length ? eventsData : [];
  const suppressions = suppressionsData.length ? suppressionsData : [];
  const operators = operatorLogData.length ? operatorLogData : [];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", gap: 0, borderBottom: "1px solid #2a2a2e" }}>{tabs.map((tab) => (<button key={tab.id} onClick={() => setAt(tab.id)} style={{ background: at === tab.id ? "#1a1a1e" : "transparent", color: at === tab.id ? "#ccc" : "#555", border: "none", borderBottom: at === tab.id ? "2px solid #7faacc" : "2px solid transparent", padding: "10px 20px", fontSize: 11, letterSpacing: 2, fontFamily: "'JetBrains Mono', monospace", fontWeight: 600, cursor: "pointer" }}>{tab.label}</button>))}</div>
      <RackPanel>
        {at === "messages" && (<><RackLabel>Message Log</RackLabel>{messages.map((msg, i) => (<div key={i} style={{ display: "flex", gap: 12, padding: "10px 0", borderBottom: "1px solid #1f1f22", alignItems: "flex-start" }}><Timestamp time={fmtTime(msg.ts)} /><Led color="#2ecc40" size={5} /><div style={{ fontSize: 12, color: "#aaa", fontFamily: "'IBM Plex Sans', sans-serif", lineHeight: 1.5 }}>{buildMessageLine(msg) || AWAITING}</div></div>))}{messages.length === 0 && <div style={{ fontSize: 12, color: "#555", fontFamily: "'IBM Plex Sans', sans-serif" }}>{AWAITING}</div>}</>)}
        {at === "suppression" && (<><RackLabel>Suppression Log</RackLabel>{suppressions.map((e, i) => (<div key={i} style={{ display: "flex", gap: 12, padding: "10px 0", borderBottom: "1px solid #1f1f22", alignItems: "flex-start" }}><Timestamp time={fmtTime(e.ts)} /><Led color="#ff851b" size={5} /><div style={{ flex: 1 }}><div style={{ fontSize: 12, color: "#aaa", fontFamily: "'IBM Plex Sans', sans-serif", lineHeight: 1.5 }}>{e.suppression_detail ? `${e.suppression_reason} (${e.suppression_detail})` : (e.suppression_reason || AWAITING)}</div><div style={{ display: "inline-block", marginTop: 4, padding: "2px 8px", background: "#ff851b12", border: "1px solid #ff851b33", borderRadius: 2, fontSize: 9, color: "#ff851b", letterSpacing: 1.5, fontFamily: "'JetBrains Mono', monospace", fontWeight: 700 }}>{e.suppression_reason || AWAITING}</div></div></div>))}{suppressions.length === 0 && <div style={{ fontSize: 12, color: "#555", fontFamily: "'IBM Plex Sans', sans-serif" }}>{AWAITING}</div>}</>)}
        {at === "operator" && (<><RackLabel>Operator Action Log</RackLabel>{operators.map((e, i) => (<div key={i} style={{ display: "flex", gap: 12, padding: "10px 0", borderBottom: "1px solid #1f1f22", alignItems: "flex-start" }}><Timestamp time={fmtTime(e.ts)} /><span style={{ fontSize: 10, padding: "2px 8px", background: e.operator === "Art" ? "#7faacc18" : "#cc7faa18", border: `1px solid ${e.operator === "Art" ? "#7faacc33" : "#cc7faa33"}`, borderRadius: 2, color: e.operator === "Art" ? "#7faacc" : "#cc7faa", fontFamily: "'JetBrains Mono', monospace", fontWeight: 700, letterSpacing: 1, minWidth: 30, textAlign: "center" }}>{String(e.operator || "").toUpperCase() || AWAITING.toUpperCase()}</span><div style={{ fontSize: 12, color: "#aaa", fontFamily: "'IBM Plex Sans', sans-serif" }}>{e.action || AWAITING}</div></div>))}{operators.length === 0 && <div style={{ fontSize: 12, color: "#555", fontFamily: "'IBM Plex Sans', sans-serif" }}>{AWAITING}</div>}</>)}
      </RackPanel>
    </div>
  );
}
function ProvidersPage({ statusData, providersStatusData, routingStatusData, systemHealthData, readinessData, setProviderActive, setProviderCaps, setRoutingEnabled, setActiveDirector, setDryRunEnabled }) {
  const providerMeta = {
    openai: { name: "OpenAI", model: "gpt-4o" },
    grok: { name: "Grok", model: "grok-3" },
    anthropic: { name: "Anthropic", model: "claude-sonnet-4-5-20250929" },
  };
  const approved = Array.isArray(providersStatusData?.approved_providers) && providersStatusData.approved_providers.length
    ? providersStatusData.approved_providers
    : [];
  const statusMetrics = providersStatusData?.metrics || {};
  const healthMetrics = systemHealthData?.providers || {};
  const hasNumericField = (obj, key) => obj && Object.prototype.hasOwnProperty.call(obj, key) && Number.isFinite(Number(obj[key]));
  const readNumericField = (obj, key) => (hasNumericField(obj, key) ? Number(obj[key]) : null);
  const providers = approved.map((id) => {
    const source = statusMetrics[id] || healthMetrics[id] || {};
    const latencyRaw = readNumericField(source, "avg_latency_ms");
    const latency = latencyRaw !== null && latencyRaw > 0 ? Math.round(latencyRaw) : null;
    return {
      id,
      name: providerMeta[id]?.name || String(id || "").toUpperCase(),
      model: providerMeta[id]?.model || "configured",
      latency,
      requests: readNumericField(source, "requests"),
      failures: readNumericField(source, "failures"),
      moderationBlocks: readNumericField(source, "moderation_blocks"),
    };
  });
  const ap = String(providersStatusData?.active_provider || "");
  const a = providers.find((p) => p.id === ap) || null;
  const usage = providersStatusData?.usage || {};
  const caps = providersStatusData?.caps || {};
  const requestsUsed = hasNumericField(usage, "requests") ? Number(usage.requests) : null;
  const requestsMax = hasNumericField(caps, "daily_requests_max") ? Number(caps.daily_requests_max) : 0;
  const tokensUsed = hasNumericField(usage, "tokens") ? Number(usage.tokens) : null;
  const dailyCostText = tokensUsed !== null && tokensUsed > 0 ? `${tokensUsed}` : AWAITING;
  const routing = routingStatusData || {};
  const routingEnabled = typeof routing.enabled === "boolean" ? routing.enabled : Boolean(statusData?.routing_enabled);
  const routingOverride = String(routing.manual_override || "default");
  const routingLast = routing.last_decision || {};
  const activeDirector = String(statusData?.active_director || providersStatusData?.active_director || "ProviderDirector");
  const dryRunEnabled = Boolean(statusData?.read_only_mode);
  const openaiHealth = healthMetrics.openai || {};
  const grokHealth = healthMetrics.grok || {};
  const healthRouting = systemHealthData?.routing || {};
  const memoryReachable = systemHealthData?.memory_db?.reachable;
  const ready = Boolean(readinessData?.ready);
  const firstBlocking = Array.isArray(readinessData?.blocking_reasons) && readinessData.blocking_reasons.length ? String(readinessData.blocking_reasons[0]) : "";
  const readinessText = ready ? "READY" : `NOT READY${firstBlocking ? ` (${firstBlocking})` : ""}`;
  const countText = (value) => (Number.isFinite(value) ? String(value) : AWAITING);
  const openaiLatency = readNumericField(openaiHealth, "avg_latency_ms");
  const grokLatency = readNumericField(grokHealth, "avg_latency_ms");
  const openaiBlocks = readNumericField(openaiHealth, "moderation_blocks");
  const grokBlocks = readNumericField(grokHealth, "moderation_blocks");
  const musicHits = readNumericField(healthRouting, "music_culture_hits");
  const generalHits = readNumericField(healthRouting, "general_hits");
  const overrideHits = readNumericField(healthRouting, "override_hits");
  const routingClass = String(routingLast.routing_class || "").trim();
  const routingModelLine = `${a?.model || AWAITING} | Routing ${routingEnabled ? "ON" : "OFF"} (${routingOverride}) | Last ${(routingClass ? routingClass.toUpperCase() : AWAITING)} | Lat O:${openaiLatency !== null ? `${Math.round(openaiLatency)}ms` : AWAITING} G:${grokLatency !== null ? `${Math.round(grokLatency)}ms` : AWAITING} | ModBlock O:${countText(openaiBlocks)} G:${countText(grokBlocks)} | Hits M:${countText(musicHits)} G:${countText(generalHits)} O:${countText(overrideHits)} | Memory ${memoryReachable === true ? "OK" : (memoryReachable === false ? "ERR" : AWAITING)} | ${readinessText}`;
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
      <RackPanel><RackLabel>Active Provider</RackLabel><div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}><Led color="#2ecc40" size={10} pulse={Boolean(a)} label="ACTIVE" /><div><div style={{ fontSize: 18, fontWeight: 700, color: "#ccc", fontFamily: "'JetBrains Mono', monospace" }}>{a?.name || AWAITING}</div><div style={{ fontSize: 11, color: "#666", fontFamily: "'JetBrains Mono', monospace", lineHeight: 1.4, wordBreak: "break-word" }}>{routingModelLine}</div></div></div><div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}><div><RackLabel>Latency (avg)</RackLabel><div style={{ fontSize: 20, fontWeight: 700, color: "#ccc", fontFamily: "'JetBrains Mono', monospace" }}>{a?.latency !== null && Number.isFinite(a?.latency) ? `${a.latency}ms` : AWAITING}</div></div><div><RackLabel>Failures</RackLabel><div style={{ fontSize: 20, fontWeight: 700, color: "#ccc", fontFamily: "'JetBrains Mono', monospace" }}>{a?.failures !== null && Number.isFinite(a?.failures) ? `${a.failures}` : AWAITING}</div></div></div>{a?.latency !== null && Number.isFinite(a?.latency) && <div style={{ marginTop: 16 }}><MeterBar value={a.latency} max={1000} color={a.latency < 500 ? "#2ecc40" : "#ff851b"} label="Response latency" /></div>}<div style={{ marginTop: 10, fontSize: 10, color: "#555", fontFamily: "'JetBrains Mono', monospace" }}>Requests: {a?.requests !== null && Number.isFinite(a?.requests) ? a.requests : AWAITING} | Moderation blocks: {a?.moderationBlocks !== null && Number.isFinite(a?.moderationBlocks) ? a.moderationBlocks : AWAITING}</div></RackPanel>
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <RackPanel><RackLabel>Usage - Today</RackLabel><div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}><div><div style={{ fontSize: 28, fontWeight: 800, color: "#ccc", fontFamily: "'JetBrains Mono', monospace", lineHeight: 1 }}>{dailyCostText}</div><div style={{ fontSize: 10, color: "#555", letterSpacing: 1.5, fontFamily: "'JetBrains Mono', monospace", marginTop: 4 }}>COST TODAY</div></div><div><div style={{ fontSize: 28, fontWeight: 800, color: "#ccc", fontFamily: "'JetBrains Mono', monospace", lineHeight: 1 }}>{requestsUsed !== null && Number.isFinite(requestsUsed) ? requestsUsed : AWAITING}</div><div style={{ fontSize: 10, color: "#555", letterSpacing: 1.5, fontFamily: "'JetBrains Mono', monospace", marginTop: 4 }}>API CALLS</div></div></div>{requestsMax > 0 && requestsUsed !== null && Number.isFinite(requestsUsed) && <MeterBar value={Math.min(requestsUsed, requestsMax)} max={requestsMax} color="#7faacc" label={`Daily request cap (${requestsMax})`} />}</RackPanel>
        <RackPanel><RackLabel>Provider Switch - Pre-Approved Only</RackLabel>{providers.map((p) => (<button key={p.id} onClick={() => setProviderActive(p.id)} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", width: "100%", background: ap === p.id ? "#2a2a2e" : "transparent", border: `1px solid ${ap === p.id ? "#7faacc44" : "#252528"}`, borderRadius: 3, padding: "10px 14px", marginBottom: 6, cursor: "pointer", boxSizing: "border-box" }}><div style={{ display: "flex", alignItems: "center", gap: 8 }}><Led color={ap === p.id ? "#2ecc40" : "#555"} size={6} /><span style={{ fontSize: 12, color: ap === p.id ? "#ccc" : "#666", fontFamily: "'JetBrains Mono', monospace", fontWeight: 600 }}>{p.name}</span></div><span style={{ fontSize: 9, letterSpacing: 1.5, color: ap === p.id ? "#2ecc40" : "#555", fontFamily: "'JetBrains Mono', monospace", fontWeight: 700 }}>{ap === p.id ? "ACTIVE" : "STANDBY"}</span></button>))}{!providers.length && <AwaitingBlock style={{ padding: "6px 0" }} />}<div style={{ marginTop: 8, display: "flex", gap: 8 }}><RackButton label={`ROUTING ${routingEnabled ? "ON" : "OFF"}`} color={routingEnabled ? "#2ecc40" : "#ff851b"} onClick={() => setRoutingEnabled(!routingEnabled)} /><RackButton label={`DIRECTOR ${activeDirector === "OfflineDirector" ? "OFFLINE" : "PROVIDER"}`} color="#7faacc" onClick={() => setActiveDirector(activeDirector === "OfflineDirector" ? "ProviderDirector" : "OfflineDirector")} /><RackButton label={`DRY_RUN ${dryRunEnabled ? "ON" : "OFF"}`} color={dryRunEnabled ? "#ff4136" : "#2ecc40"} onClick={() => setDryRunEnabled(!dryRunEnabled)} /></div></RackPanel>
      </div>
    </div>
  );
}

// --- PAGE: AUTH & ACCOUNTS ---

function AuthPage({ twitchStatusData, twitchConnectStart, twitchDisconnect, twitchNotice, setTwitchNotice }) {
  const accounts = twitchStatusData?.accounts || {};
  const bot = accounts.bot || {};
  const broadcaster = accounts.broadcaster || {};
  const scopesPresent = twitchStatusData?.scopes_present || {};
  const missingConfigFields = Array.isArray(twitchStatusData?.missing_config_fields) ? twitchStatusData.missing_config_fields : [];
  const primaryChannelRaw = String(twitchStatusData?.primary_channel || "").trim();
  const primaryChannel = primaryChannelRaw ? `#${primaryChannelRaw.replace(/^#/, "")}` : "";
  const caps = [
    { name: "Read chat events", on: Boolean(scopesPresent["chat:read"]) },
    { name: "Send chat messages", on: Boolean(scopesPresent["chat:edit"]) },
    { name: "Read schedule", on: undefined },
    { name: "Whisper control", on: undefined },
  ];
  const lastRefresh = twitchStatusData?.last_checked_ts ? fmtTime(twitchStatusData.last_checked_ts) : AWAITING;
  const healthOk = Boolean(bot.connected || broadcaster.connected);
  const healthLabel = healthOk ? "OK" : AWAITING;

  const AcctRow = ({ account }) => {
    const hasConnectedState = typeof account?.connected === "boolean";
    const connected = hasConnectedState ? account.connected : null;
    const reason = String(account?.reason || "").trim();
    const reasonText = reason ? reason.replaceAll("_", " ") : AWAITING;
    const accountName = String(account?.display_name || "").trim() || AWAITING;
    const accountRole = String(account?.role || "").trim() || AWAITING;
    const accountId = String(account?.account || "").toLowerCase();
    const canDisconnect = Boolean(account?.disconnect_available && accountId);
    const canConnect = Boolean(account?.connect_available && accountId);
    const statusLabel = connected === true ? "CONNECTED" : (connected === false ? "DISCONNECTED" : AWAITING.toUpperCase());
    return (
      <div style={{ marginBottom: 16 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <Led color={connected === true ? "#2ecc40" : (connected === false ? "#ff4136" : "#555")} size={8} pulse={connected === true} />
            <div>
              <div style={{ fontSize: 13, fontWeight: 700, color: "#ccc", fontFamily: "'JetBrains Mono', monospace" }}>{accountName}</div>
              <div style={{ fontSize: 10, color: "#555", fontFamily: "'JetBrains Mono', monospace", letterSpacing: 1 }}>{accountRole}</div>
            </div>
          </div>
          <span style={{ fontSize: 9, padding: "3px 8px", background: connected === true ? "#2ecc4012" : (connected === false ? "#ff413612" : "#2a2a2e"), border: `1px solid ${connected === true ? "#2ecc4033" : (connected === false ? "#ff413633" : "#333")}`, borderRadius: 2, color: connected === true ? "#2ecc40" : (connected === false ? "#ff4136" : "#666"), letterSpacing: 1.5, fontFamily: "'JetBrains Mono', monospace", fontWeight: 700 }}>{statusLabel}</span>
        </div>
        <div style={{ ...TEXT_STYLES.meta, marginBottom: 8 }}>
          {connected === true ? "Status verified by backend." : (connected === false ? `Reason: ${reasonText || AWAITING}` : AWAITING)}
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <RackButton
            label={canConnect ? (connected === true ? "RECONNECT" : "CONNECT") : "NOT AVAILABLE"}
            color="#7faacc"
            disabled={!canConnect}
            onClick={async () => {
              const result = await twitchConnectStart(accountId);
              const authUrl = result?.body?.auth_url;
              if (authUrl) {
                const popup = window.open(authUrl, "_blank", "popup,width=560,height=760");
                if (!popup) {
                  setTwitchNotice(`Popup blocked. Open this URL manually: ${authUrl}`);
                } else {
                  setTwitchNotice("Twitch authorization started in a new tab.");
                }
              } else if (!result?.ok) {
                const detail = String(result?.body?.detail || result?.body?.error || AWAITING);
                setTwitchNotice(detail);
                console.error(`[Dashboard Twitch] reconnect unavailable for ${accountId}:`, detail);
              }
            }}
          />
          <RackButton
            label={canDisconnect ? "DISCONNECT" : "NOT AVAILABLE"}
            color="#ff4136"
            disabled={!canDisconnect}
            onClick={() => twitchDisconnect(accountId)}
          />
        </div>
      </div>
    );
  };

  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
      <RackPanel>
        <RackLabel>Accounts</RackLabel>
        <AcctRow account={bot} />
        <div style={{ borderTop: "1px solid #222", paddingTop: 12 }} />
        <AcctRow account={broadcaster} />
      </RackPanel>
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <RackPanel>
          <RackLabel>Capabilities</RackLabel>
          {caps.map((c, i) => (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: 10, padding: "7px 0", borderBottom: i < caps.length - 1 ? "1px solid #1f1f22" : "none" }}>
              <Led color={c.on === true ? "#2ecc40" : "#555"} size={5} />
              <span style={{ fontSize: 12, color: c.on === true ? "#aaa" : "#555", fontFamily: "'IBM Plex Sans', sans-serif" }}>{c.name}</span>
              {c.on !== true && <AwaitingInline style={{ marginLeft: "auto" }} />}
            </div>
          ))}
        </RackPanel>
        <RackPanel>
          <RackLabel>Auth Health</RackLabel>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
            <Led color={healthOk ? "#2ecc40" : "#ff851b"} size={8} pulse={healthOk} />
            <span style={{ fontSize: 13, fontWeight: 700, color: healthOk ? "#2ecc40" : "#ff851b", fontFamily: "'JetBrains Mono', monospace", letterSpacing: 1.5 }}>{healthLabel}</span>
          </div>
          <RackLabel>Primary Channel</RackLabel>
          <div style={{ ...TEXT_STYLES.body, marginBottom: 8 }}>{primaryChannel || AWAITING}</div>
          <RackLabel>Missing Config</RackLabel>
          <div style={{ ...TEXT_STYLES.muted, marginBottom: 10 }}>
            {missingConfigFields.length ? missingConfigFields.join(", ") : "none"}
          </div>
          <RackLabel>Last Refresh</RackLabel>
          <div style={{ fontSize: 12, color: "#888", fontFamily: "'JetBrains Mono', monospace" }}>{lastRefresh}</div>
        </RackPanel>
        <div style={{ padding: "10px 14px", background: "#15151a", border: "1px solid #1f1f22", borderRadius: 3 }}>
          <div style={TEXT_STYLES.meta}>{twitchNotice || "Auth status is backend-verified only."}</div>
        </div>
      </div>
    </div>
  );
}

// --- PAGE: CULTURAL NOTES ---

function CulturePage({
  culturalNotesData = [],
  viewerNotesData = [],
  memoryPendingData = [],
  saveCulturalNote,
  deleteCulturalNote,
  saveViewerNote,
  deleteViewerNote,
  reviewMemoryPending,
}) {
  const culturalNotes = Array.isArray(culturalNotesData) ? culturalNotesData : [];
  const viewerNotes = Array.isArray(viewerNotesData) ? viewerNotesData : [];
  const pendingNotes = Array.isArray(memoryPendingData) ? memoryPendingData : [];

  const addCulturalNote = async () => {
    const note = window.prompt("Add cultural note");
    if (note == null) return;
    const text = String(note).trim();
    if (!text) return;
    await saveCulturalNote(text, { method: "POST", tags: [] });
  };

  const editCulturalNote = async (item) => {
    if (!item?.id) return;
    const next = window.prompt("Edit cultural note (blank = delete)", String(item.note || ""));
    if (next == null) return;
    const text = String(next).trim();
    if (!text) {
      if (window.confirm("Delete this cultural note?")) {
        await deleteCulturalNote(item.id);
      }
      return;
    }
    await saveCulturalNote(text, { method: "PATCH", id: item.id, tags: Array.isArray(item.tags) ? item.tags : [] });
  };

  const addViewerNote = async () => {
    const handleRaw = window.prompt("Viewer handle (without @ preferred)");
    if (handleRaw == null) return;
    const viewerHandle = String(handleRaw).trim().replace(/^@+/, "");
    if (!viewerHandle) return;
    const noteRaw = window.prompt(`Viewer note for @${viewerHandle}`);
    if (noteRaw == null) return;
    const note = String(noteRaw).trim();
    if (!note) return;
    await saveViewerNote({ viewer_handle: viewerHandle, note, tags: [] }, { method: "POST" });
  };

  const editViewerNote = async (item) => {
    if (!item?.id) return;
    const next = window.prompt("Edit viewer note (blank = delete)", String(item.note || ""));
    if (next == null) return;
    const text = String(next).trim();
    if (!text) {
      if (window.confirm("Delete this viewer note?")) {
        await deleteViewerNote(item.id);
      }
      return;
    }
    await saveViewerNote(
      { note: text, tags: Array.isArray(item.tags) ? item.tags : [], is_active: item.is_active !== false },
      { method: "PATCH", id: item.id },
    );
  };

  const approvePending = async (item) => {
    if (!item?.id) return;
    await reviewMemoryPending(item.id, "approve");
  };

  const denyPending = async (item) => {
    if (!item?.id) return;
    const reasonRaw = window.prompt("Optional deny reason");
    if (reasonRaw == null) return;
    await reviewMemoryPending(item.id, "deny", String(reasonRaw || "").trim());
  };

  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
      <RackPanel>
        <RackLabel>Roonie Proposed Notes - Review</RackLabel>
        <div style={{ fontSize: 10, color: "#555", fontFamily: "'IBM Plex Sans', sans-serif", marginBottom: 10 }}>
          Review each candidate and approve or deny before it becomes memory.
        </div>
        {pendingNotes.map((item) => (
          <div key={item.id} style={{ padding: "10px 12px", marginBottom: 6, background: "#15151a", border: "1px solid #222", borderRadius: 2 }}>
            <div style={{ fontSize: 11, color: "#7faacc", fontFamily: "'JetBrains Mono', monospace", fontWeight: 700, marginBottom: 4 }}>
              @{item.viewer_handle || AWAITING}
            </div>
            <div style={{ fontSize: 12, color: "#999", fontFamily: "'IBM Plex Sans', sans-serif", lineHeight: 1.4, marginBottom: 8 }}>
              {item.proposed_note || AWAITING}
            </div>
            <div style={{ display: "flex", gap: 6 }}>
              <button onClick={() => approvePending(item)} style={{ background: "none", border: "1px solid #2ecc4044", borderRadius: 2, color: "#2ecc40", padding: "2px 8px", fontSize: 10, cursor: "pointer", fontFamily: "'JetBrains Mono', monospace" }}>APPROVE</button>
              <button onClick={() => denyPending(item)} style={{ background: "none", border: "1px solid #ff851b44", borderRadius: 2, color: "#ff851b", padding: "2px 8px", fontSize: 10, cursor: "pointer", fontFamily: "'JetBrains Mono', monospace" }}>DENY</button>
            </div>
          </div>
        ))}
        {!pendingNotes.length ? <div style={{ padding: "6px 0", fontSize: 11, color: "#666", fontFamily: "'IBM Plex Sans', sans-serif" }}>{AWAITING}</div> : null}

        <div style={{ borderTop: "1px solid #1f1f22", margin: "10px 0" }} />
        <RackLabel>Cultural Notes - Room Level</RackLabel>
        <div style={{ fontSize: 10, color: "#555", fontFamily: "'IBM Plex Sans', sans-serif", marginBottom: 12 }}>These shape how Roonie reads the room and responds. Apply to all interactions.</div>
        {culturalNotes.map((item) => (
          <div key={item.id} onClick={() => editCulturalNote(item)} style={{ padding: "10px 12px", borderLeft: "2px solid #7faacc44", marginBottom: 6, background: "#15151a", borderRadius: "0 2px 2px 0", cursor: "pointer" }}>
            <div style={{ fontSize: 12, color: "#aaa", fontFamily: "'IBM Plex Sans', sans-serif", lineHeight: 1.5 }}>{item.note}</div>
          </div>
        ))}
        {!culturalNotes.length ? <div style={{ padding: "6px 0", fontSize: 11, color: "#666", fontFamily: "'IBM Plex Sans', sans-serif" }}>{AWAITING}</div> : null}
        <button onClick={addCulturalNote} style={{ marginTop: 8, background: "transparent", border: "1px dashed #333", color: "#555", padding: "8px 16px", fontSize: 10, letterSpacing: 1.5, fontFamily: "'JetBrains Mono', monospace", cursor: "pointer", borderRadius: 2, width: "100%" }}>+ ADD CULTURAL NOTE</button>
      </RackPanel>
      <RackPanel><RackLabel>Viewer Notes - Individual</RackLabel><div style={{ fontSize: 10, color: "#555", fontFamily: "'IBM Plex Sans', sans-serif", marginBottom: 12 }}>Observable behavior only. No subjective labels or inferred traits.</div>{viewerNotes.map((v) => (<div key={v.id} style={{ padding: "10px 12px", marginBottom: 6, background: "#15151a", border: "1px solid #222", borderRadius: 2, display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 10 }}><div style={{ flex: 1 }}><div style={{ fontSize: 11, color: "#7faacc", fontFamily: "'JetBrains Mono', monospace", fontWeight: 700, marginBottom: 4 }}>@{v.viewer_handle}</div><div style={{ fontSize: 12, color: "#999", fontFamily: "'IBM Plex Sans', sans-serif", lineHeight: 1.4 }}>{v.note}</div></div><div style={{ display: "flex", gap: 4, flexShrink: 0 }}><button onClick={() => editViewerNote(v)} style={{ background: "none", border: "1px solid #333", borderRadius: 2, color: "#666", padding: "2px 6px", fontSize: 10, cursor: "pointer", fontFamily: "'JetBrains Mono', monospace" }}>EDIT</button><button onClick={() => deleteViewerNote(v.id)} style={{ background: "none", border: "1px solid #ff413633", borderRadius: 2, color: "#ff4136", padding: "2px 6px", fontSize: 10, cursor: "pointer", fontFamily: "'JetBrains Mono', monospace" }}>X</button></div></div>))}{!viewerNotes.length ? <div style={{ padding: "6px 0", fontSize: 11, color: "#666", fontFamily: "'IBM Plex Sans', sans-serif" }}>{AWAITING}</div> : null}<button onClick={addViewerNote} style={{ marginTop: 8, background: "transparent", border: "1px dashed #333", color: "#555", padding: "8px 16px", fontSize: 10, letterSpacing: 1.5, fontFamily: "'JetBrains Mono', monospace", cursor: "pointer", borderRadius: 2, width: "100%" }}>+ ADD VIEWER NOTE</button></RackPanel>
    </div>
  );
}

// --- PAGE: CULTURAL SNAPSHOT ---

function CulturalSnapshotPage({ logsEventsData = [], logsSuppressionsData = [], statusData = {} }) {
  const events = Array.isArray(logsEventsData) ? logsEventsData.slice(0, 5) : [];
  const suppressions = Array.isArray(logsSuppressionsData) ? logsSuppressionsData.slice(0, 5) : [];
  const directAddressCount = events.filter((e) => Boolean(e?.direct_address)).length;
  const speakCount = events.filter((e) => String(e?.decision_type || "").toLowerCase() === "speak").length;
  const suppressionCount = suppressions.length;
  const contextText = statusData?.context_last_active
    ? `${Number(statusData?.context_last_turns_used || 0)} turn carry active`
    : AWAITING;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div>
          <div style={{ fontSize: 14, fontWeight: 700, color: "#ccc", fontFamily: "'JetBrains Mono', monospace", letterSpacing: 2 }}>CULTURAL SNAPSHOT</div>
          <div style={{ fontSize: 10, color: "#555", fontFamily: "'JetBrains Mono', monospace", letterSpacing: 1.5, marginTop: 4 }}>READ-ONLY SIGNALS FROM LIVE LOGS</div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Led color="#7faacc" size={6} />
          <span style={{ fontSize: 10, color: "#7faacc", fontFamily: "'JetBrains Mono', monospace", letterSpacing: 1.5, fontWeight: 600 }}>READ-ONLY | POST-SESSION</span>
        </div>
      </div>

      <RackPanel>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          <div>
            <RackLabel>Session Metrics</RackLabel>
            <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 14 }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <span style={{ fontSize: 11, color: "#888", fontFamily: "'IBM Plex Sans', sans-serif" }}>Events Sampled</span>
                <span style={{ fontSize: 11, color: "#aaa", fontFamily: "'JetBrains Mono', monospace" }}>{events.length ? events.length : <AwaitingInline />}</span>
              </div>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <span style={{ fontSize: 11, color: "#888", fontFamily: "'IBM Plex Sans', sans-serif" }}>Direct Address Count</span>
                <span style={{ fontSize: 11, color: "#aaa", fontFamily: "'JetBrains Mono', monospace" }}>{events.length ? directAddressCount : <AwaitingInline />}</span>
              </div>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <span style={{ fontSize: 11, color: "#888", fontFamily: "'IBM Plex Sans', sans-serif" }}>Spoken Decisions</span>
                <span style={{ fontSize: 11, color: "#aaa", fontFamily: "'JetBrains Mono', monospace" }}>{events.length ? speakCount : <AwaitingInline />}</span>
              </div>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <span style={{ fontSize: 11, color: "#888", fontFamily: "'IBM Plex Sans', sans-serif" }}>Suppression Count</span>
                <span style={{ fontSize: 11, color: "#aaa", fontFamily: "'JetBrains Mono', monospace" }}>{suppressions.length ? suppressionCount : <AwaitingInline />}</span>
              </div>
            </div>

            <RackLabel>Recent Events</RackLabel>
            {events.map((event, idx) => (
              <div key={idx} style={{ padding: "6px 10px", borderLeft: "2px solid #7faacc33", marginBottom: 4, background: "#15151a", borderRadius: "0 2px 2px 0" }}>
                <div style={{ fontSize: 10, color: "#7faacc", fontFamily: "'JetBrains Mono', monospace", fontWeight: 600, marginBottom: 2 }}>{fmtTime(event.ts)}</div>
                <div style={{ fontSize: 11, color: "#999", fontFamily: "'IBM Plex Sans', sans-serif", lineHeight: 1.5 }}>{buildMessageLine(event) || AWAITING}</div>
              </div>
            ))}
            {!events.length ? <div style={{ fontSize: 11, color: "#666", fontFamily: "'IBM Plex Sans', sans-serif" }}>{AWAITING}</div> : null}
          </div>

          <div>
            <RackLabel>Room Tone</RackLabel>
            <div style={{ marginBottom: 14 }}>
              <div style={{ padding: "5px 0", borderBottom: "1px solid #1f1f22" }}>
                <div style={{ fontSize: 9, color: "#5a5a5a", letterSpacing: 1.5, fontFamily: "'JetBrains Mono', monospace", marginBottom: 2 }}>Context Carry</div>
                <div style={{ fontSize: 11, color: "#aaa", fontFamily: "'IBM Plex Sans', sans-serif" }}>{contextText}</div>
              </div>
              <div style={{ padding: "5px 0", borderBottom: "1px solid #1f1f22" }}>
                <div style={{ fontSize: 9, color: "#5a5a5a", letterSpacing: 1.5, fontFamily: "'JetBrains Mono', monospace", marginBottom: 2 }}>Last Suppression</div>
                <div style={{ fontSize: 11, color: "#aaa", fontFamily: "'IBM Plex Sans', sans-serif" }}>{suppressions[0]?.suppression_reason || AWAITING}</div>
              </div>
              <div style={{ padding: "5px 0" }}>
                <div style={{ fontSize: 9, color: "#5a5a5a", letterSpacing: 1.5, fontFamily: "'JetBrains Mono', monospace", marginBottom: 2 }}>Last Suppression Detail</div>
                <div style={{ fontSize: 11, color: "#aaa", fontFamily: "'IBM Plex Sans', sans-serif" }}>{suppressions[0]?.suppression_detail || AWAITING}</div>
              </div>
            </div>

            <RackLabel>Suppression Reasons</RackLabel>
            {suppressions.map((entry, idx) => (
              <div key={idx} style={{ display: "flex", justifyContent: "space-between", padding: "3px 0" }}>
                <span style={{ fontSize: 10, color: "#666", fontFamily: "'JetBrains Mono', monospace" }}>{fmtTime(entry.ts)}</span>
                <span style={{ fontSize: 10, color: "#aaa", fontFamily: "'JetBrains Mono', monospace" }}>{entry.suppression_reason || AWAITING}</span>
              </div>
            ))}
            {!suppressions.length ? <div style={{ fontSize: 11, color: "#666", fontFamily: "'IBM Plex Sans', sans-serif" }}>{AWAITING}</div> : null}
            <div style={{ fontSize: 9, color: "#555", fontFamily: "'IBM Plex Sans', sans-serif", marginTop: 8 }}>Derived from logs only. No automatic behavior changes.</div>
          </div>
        </div>
      </RackPanel>

      <div style={{ padding: "16px 20px", background: "#15151a", border: "1px solid #1f1f22", borderRadius: 3 }}>
        <RackLabel>Policy Constraints</RackLabel>
        {[
          "This system does NOT change personality automatically.",
          "It does NOT learn slang.",
          "It does NOT rewrite persona rules.",
          "It does NOT modify live behavior without operator approval.",
          "All adjustments remain within locked system limits.",
          "All changes are logged and reversible.",
        ].map((rule, i) => (
          <div key={i} style={{ padding: "4px 0", fontSize: 11, color: "#666", fontFamily: "'IBM Plex Sans', sans-serif", lineHeight: 1.6 }}>
            {rule}
          </div>
        ))}
      </div>
    </div>
  );
}

// --- PAGE: SENSES ---

function SensesPage({ sensesStatusData }) {
  const senses = [{ name: "Audio Analysis", desc: "Real-time frequency / BPM detection from stream audio" }, { name: "Visual Feed", desc: "Camera feed analysis for crowd or gesture detection" }, { name: "Emotion Detection", desc: "Chat sentiment beyond keyword matching" }, { name: "Voice Recognition", desc: "Operator voice commands via microphone" }];
  const status = sensesStatusData || {};
  const whitelist = Array.isArray(status.whitelist) && status.whitelist.length ? status.whitelist : null;
  const guardrailsText = `Local-only: ${typeof status.local_only === "boolean" ? (status.local_only ? "yes" : "no") : AWAITING} | Whitelist: ${whitelist ? whitelist.join(", ") : AWAITING} | Never initiate: ${typeof status.never_initiate === "boolean" ? (status.never_initiate ? "yes" : "no") : AWAITING} | No viewer recognition: ${typeof status.no_viewer_recognition === "boolean" ? (status.no_viewer_recognition ? "yes" : "no") : AWAITING}`;
  const statusReason = status.reason || AWAITING;
  const purpose = status.purpose || AWAITING;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ background: "#ff413608", border: "2px solid #ff413644", borderRadius: 4, padding: "20px 28px", display: "flex", alignItems: "center", gap: 16 }}><div style={{ width: 40, height: 40, borderRadius: "50%", border: "3px solid #ff4136", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 18, color: "#ff4136", flexShrink: 0 }}>OFF</div><div><div style={{ fontSize: 16, fontWeight: 800, color: "#ff4136", letterSpacing: 3, fontFamily: "'JetBrains Mono', monospace" }}>ALL SENSES CURRENTLY OFF</div><div style={{ fontSize: 12, color: "#666", fontFamily: "'IBM Plex Sans', sans-serif", marginTop: 4, lineHeight: 1.5 }}>Roonie operates on text input only. Purpose: {purpose}. Future senses remain OFF by Canon until explicitly approved.</div></div></div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>{senses.map((s) => (<div key={s.name} style={{ background: "#1a1a1e", border: "1px solid #2a2a2e", borderRadius: 3, padding: 16, position: "relative", opacity: 0.35, pointerEvents: "none", userSelect: "none" }}><div style={{ position: "absolute", top: 6, left: 8, width: 4, height: 4, borderRadius: "50%", background: "#252528", border: "1px solid #333" }} /><div style={{ position: "absolute", top: 6, right: 8, width: 4, height: 4, borderRadius: "50%", background: "#252528", border: "1px solid #333" }} /><div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}><RackLabel>{s.name}</RackLabel><span style={{ fontSize: 9, padding: "3px 8px", background: "#ff413612", border: "1px solid #ff413633", borderRadius: 2, color: "#ff4136", letterSpacing: 1.5, fontFamily: "'JetBrains Mono', monospace", fontWeight: 700 }}>HARD OFF</span></div><div style={{ fontSize: 11, color: "#555", fontFamily: "'IBM Plex Sans', sans-serif", lineHeight: 1.5, marginBottom: 12 }}>{s.desc}</div><div style={{ borderTop: "1px solid #222", paddingTop: 8 }}><div style={{ fontSize: 9, color: "#444", letterSpacing: 1.5, fontFamily: "'JetBrains Mono', monospace" }}>GUARDRAILS</div><div style={{ fontSize: 10, color: "#555", fontFamily: "'IBM Plex Sans', sans-serif", marginTop: 4 }}>{guardrailsText}</div></div></div>))}</div>
      <div style={{ padding: "12px 16px", background: "#15151a", border: "1px solid #1f1f22", borderRadius: 3, textAlign: "center" }}><div style={{ fontSize: 10, color: "#444", fontFamily: "'JetBrains Mono', monospace", letterSpacing: 1.5 }}>ENABLING ANY SENSE REQUIRES A CODE-LEVEL CHANGE AND OPERATOR CONSENSUS</div><div style={{ fontSize: 10, color: "#333", fontFamily: "'JetBrains Mono', monospace", letterSpacing: 1, marginTop: 4 }}>{statusReason}</div></div>
    </div>
  );
}

// --- PAGE: SETTINGS & GOVERNANCE ---

function GovernancePage() {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
      <RackPanel><RackLabel>Locked Behavior - Read Only</RackLabel><div style={{ fontSize: 10, color: "#555", fontFamily: "'IBM Plex Sans', sans-serif", marginBottom: 12 }}>These constraints are enforced at startup and cannot be changed from the dashboard.</div>{["Roonie never initiates conversation unprompted.", "Roonie never uses exclamation-heavy or hype language.", "Roonie goes quieter when chat gets louder - never louder.", "Roonie does not reference operator names unless asked.", "Roonie does not guess track IDs - uncertainty is stated.", "Roonie does not store or surface personal viewer data beyond operator notes.", "Silence and restraint are features, not failures."].map((rule, i) => (<div key={i} style={{ padding: "8px 12px", marginBottom: 4, background: "#15151a", borderLeft: "2px solid #2ecc4044", borderRadius: "0 2px 2px 0" }}><div style={{ fontSize: 12, color: "#aaa", fontFamily: "'IBM Plex Sans', sans-serif", lineHeight: 1.5 }}>{rule}</div></div>))}</RackPanel>
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <RackPanel><RackLabel>Persona Source</RackLabel><div style={{ padding: "10px 12px", background: "#15151a", border: "1px solid #222", borderRadius: 2, marginBottom: 8 }}><div style={{ fontSize: 12, color: "#aaa", fontFamily: "'IBM Plex Sans', sans-serif", lineHeight: 1.5 }}>Persona is defined in versioned policy files loaded at startup.</div></div><div style={{ padding: "10px 12px", background: "#15151a", border: "1px solid #222", borderRadius: 2 }}><div style={{ fontSize: 12, color: "#aaa", fontFamily: "'IBM Plex Sans', sans-serif", lineHeight: 1.5 }}>Dashboard only edits bounded Style Profile values.</div></div><div style={{ fontSize: 9, color: "#444", fontFamily: "'JetBrains Mono', monospace", marginTop: 10, letterSpacing: 1 }}>NO FREE-TEXT PERSONA EDITOR EXISTS BY DESIGN</div></RackPanel>
        <RackPanel><RackLabel>Anti-Drift</RackLabel><div style={{ fontSize: 10, color: "#555", fontFamily: "'IBM Plex Sans', sans-serif", marginBottom: 12 }}>This section exists to prevent scope creep. If something is not listed here, it is not in scope.</div>{[{ label: "Persona editing", status: "LOCKED" }, { label: "Auto-message scheduling", status: "NOT SUPPORTED" }, { label: "Viewer profiling", status: "NOT SUPPORTED" }, { label: "Deep configuration knobs", status: "NOT EXPOSED" }].map((item, i) => (<div key={i} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "7px 0", borderBottom: i < 3 ? "1px solid #1f1f22" : "none" }}><span style={{ fontSize: 12, color: "#888", fontFamily: "'IBM Plex Sans', sans-serif" }}>{item.label}</span><span style={{ fontSize: 9, padding: "2px 8px", background: "#ff851b12", border: "1px solid #ff851b33", borderRadius: 2, color: "#ff851b", letterSpacing: 1.5, fontFamily: "'JetBrains Mono', monospace", fontWeight: 700 }}>{item.status}</span></div>))}</RackPanel>
      </div>
    </div>
  );
}

// --- MAIN APP ---

export default function RoonieControlRoom() {
  const [activePage, setActivePage] = useState("live");
  const [clock, setClock] = useState("");
  const {
    authData,
    authChecked,
    statusData,
    eventsData,
    suppressionsData,
    operatorLogData,
    queueData,
    studioProfileData,
    logsEventsData,
    logsSuppressionsData,
    logsOperatorData,
    libraryStatusData,
    librarySearchData,
    providersStatusData,
    systemHealthData,
    readinessData,
    routingStatusData,
    sensesStatusData,
    culturalNotesData,
    viewerNotesData,
    memoryPendingData,
    twitchStatusData,
    twitchNotice,
    setTwitchNotice,
    performAction,
    saveStudioProfile,
    searchLibraryIndex,
    uploadLibraryXml,
    setProviderActive,
    setProviderCaps,
    setRoutingEnabled,
    setActiveDirector,
    setDryRunEnabled,
    saveCulturalNote,
    deleteCulturalNote,
    saveViewerNote,
    deleteViewerNote,
    reviewMemoryPending,
    loginDashboard,
    logoutDashboard,
    twitchConnectStart,
    twitchDisconnect,
  } = useDashboardData();
  const [authBusy, setAuthBusy] = useState(false);
  const [authError, setAuthError] = useState("");

  const handleAuthLogin = async (username, password, rememberLogin) => {
    setAuthBusy(true);
    setAuthError("");
    const result = await loginDashboard(username, password, rememberLogin);
    if (!result.ok) {
      const detail = result?.body?.detail || result?.body?.error || "Login failed";
      setAuthError(String(detail));
    }
    setAuthBusy(false);
  };

  const handleAuthLogout = async () => {
    await logoutDashboard();
    setAuthError("");
  };

  useEffect(() => {
    const tick = () => setClock(new Date().toLocaleTimeString("en-US", { hour12: false }));
    tick();
    const interval = setInterval(tick, 1000);
    return () => clearInterval(interval);
  }, []);

  const renderPage = () => {
    switch (activePage) {
      case "live": return <LivePage statusData={statusData} eventsData={eventsData} suppressionsData={suppressionsData} performAction={performAction} />;
      case "studio": return <StudioProfilePage studioProfileData={studioProfileData} saveStudioProfile={saveStudioProfile} />;
      case "library": return <LibraryIndexPage libraryStatusData={libraryStatusData} librarySearchData={librarySearchData} searchLibraryIndex={searchLibraryIndex} uploadLibraryXml={uploadLibraryXml} />;
      case "announcements": return <AnnouncementsPage queueData={queueData} performAction={performAction} />;
      case "nowplaying": return <NowPlayingPage />;
      case "logs": return <LogsPage eventsData={logsEventsData} suppressionsData={logsSuppressionsData} operatorLogData={logsOperatorData} />;
      case "providers": return <ProvidersPage statusData={statusData} providersStatusData={providersStatusData} routingStatusData={routingStatusData} systemHealthData={systemHealthData} readinessData={readinessData} setProviderActive={setProviderActive} setProviderCaps={setProviderCaps} setRoutingEnabled={setRoutingEnabled} setActiveDirector={setActiveDirector} setDryRunEnabled={setDryRunEnabled} />;
      case "auth": return <AuthPage twitchStatusData={twitchStatusData} twitchConnectStart={twitchConnectStart} twitchDisconnect={twitchDisconnect} twitchNotice={twitchNotice} setTwitchNotice={setTwitchNotice} />;
      case "culture": return <CulturePage culturalNotesData={culturalNotesData} viewerNotesData={viewerNotesData} memoryPendingData={memoryPendingData} saveCulturalNote={saveCulturalNote} deleteCulturalNote={deleteCulturalNote} saveViewerNote={saveViewerNote} deleteViewerNote={deleteViewerNote} reviewMemoryPending={reviewMemoryPending} />;
      case "snapshot": return <CulturalSnapshotPage logsEventsData={logsEventsData} logsSuppressionsData={logsSuppressionsData} statusData={statusData} />;
      case "senses": return <SensesPage sensesStatusData={sensesStatusData} />;
      case "governance": return <GovernancePage />;
      default: return <LivePage statusData={statusData} eventsData={eventsData} suppressionsData={suppressionsData} performAction={performAction} />;
    }
  };

  const sessionUsername = authData?.authenticated
    ? String(authData?.username || "").trim()
    : "";
  const headerUserLabel = sessionUsername ? sessionUsername.toUpperCase() : "GUEST";

  return (
    <div style={{ minHeight: "100vh", background: "#111114", color: "#ccc", fontFamily: "'IBM Plex Sans', sans-serif", display: "flex", flexDirection: "column" }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500;600;700;800&display=swap');
        @keyframes ledPulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        ::-webkit-scrollbar { width: 4px; }
        ::-webkit-scrollbar-track { background: #111114; }
        ::-webkit-scrollbar-thumb { background: #333; border-radius: 2px; }
        input::placeholder { color: #444; }
        input:focus { border-color: #7faacc !important; }
        button:hover { opacity: 0.85; }
      `}</style>
      <DashboardAuthOverlay
        visible={!authChecked || !authData?.authenticated}
        busy={authBusy}
        errorText={authError}
        onSubmit={handleAuthLogin}
      />

      {/* --- TOP BAR --- */}
      <div style={{
        height: 52, background: "#0d0d10", borderBottom: "1px solid #1f1f22",
        display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "0 20px", flexShrink: 0, gap: 16, overflow: "hidden",
      }}>
        {/* Left: brand */}
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexShrink: 0 }}>
          <div style={{ width: 6, height: 6, borderRadius: "50%", background: "#2ecc40", boxShadow: "0 0 6px #2ecc40" }} />
          <span style={{ fontSize: 12, fontWeight: 800, letterSpacing: 4, color: "#888", fontFamily: "'JetBrains Mono', monospace" }}>ROONIE</span>
        </div>

        {/* Center: Now Playing */}
        <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", justifyContent: "center", gap: 1, overflow: "hidden" }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 8, whiteSpace: "nowrap" }}>
            <span style={{ fontSize: 8, color: "#555", letterSpacing: 2, fontFamily: "'JetBrains Mono', monospace", flexShrink: 0 }}>CURRENTLY PLAYING</span>
            <span style={{ fontSize: 11, color: "#ccc", fontFamily: "'IBM Plex Sans', sans-serif", fontWeight: 600 }}>{AWAITING}</span>
          </div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 8, whiteSpace: "nowrap" }}>
            <span style={{ fontSize: 8, color: "#444", letterSpacing: 2, fontFamily: "'JetBrains Mono', monospace", flexShrink: 0 }}>PREVIOUS</span>
            <span style={{ fontSize: 10, color: "#666", fontFamily: "'IBM Plex Sans', sans-serif" }}>{AWAITING}</span>
          </div>
        </div>

        {/* Right: operator + clock */}
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexShrink: 0 }}>
          <span style={{ fontSize: 10, color: "#555", fontFamily: "'JetBrains Mono', monospace", letterSpacing: 1 }}>{headerUserLabel}</span>
          {authData?.authenticated ? (
            <button
              onClick={handleAuthLogout}
              style={{
                background: "none",
                border: "1px solid #2a2a2e",
                borderRadius: 2,
                color: "#666",
                fontSize: 9,
                letterSpacing: 1.2,
                fontFamily: "'JetBrains Mono', monospace",
                padding: "3px 6px",
                cursor: "pointer",
              }}
            >
              SIGN OUT
            </button>
          ) : null}
          <span style={{ fontSize: 12, color: "#666", fontFamily: "'JetBrains Mono', monospace", fontVariantNumeric: "tabular-nums" }}>{clock}</span>
        </div>
      </div>

      <div style={{ display: "flex", flex: 1 }}>
        {/* --- SIDEBAR --- */}
        <nav style={{ width: 180, background: "#0d0d10", borderRight: "1px solid #1f1f22", padding: "12px 0", flexShrink: 0, display: "flex", flexDirection: "column" }}>
          {PAGES_TOP.map((page) => <NavButton key={page.id} page={page} activePage={activePage} setActivePage={setActivePage} />)}
          <div style={{ flex: 1 }} />
          <div style={{ borderTop: "1px solid #1f1f22", margin: "4px 16px" }} />
          {PAGES_BOTTOM.map((page) => <NavButton key={page.id} page={page} activePage={activePage} setActivePage={setActivePage} />)}
          <div style={{ padding: "12px 16px", borderTop: "1px solid #1f1f22", marginTop: 4 }}>
            <div style={{ fontSize: 9, color: "#333", letterSpacing: 1.5, fontFamily: "'JetBrains Mono', monospace", lineHeight: 1.8 }}>
              <div>STREAM: {String(statusData.mode || "offline").toUpperCase()}</div>
              <div>TWITCH: {statusData.twitch_connected ? "CONNECTED" : "DISCONNECTED"}</div>
              <div>PROVIDER: {(statusData.active_provider ? String(statusData.active_provider).toUpperCase() : AWAITING.toUpperCase())}</div>
              <div>DIRECTOR: {String(statusData.active_director || "ProviderDirector").toUpperCase()}</div>
              <div>ROUTING: {statusData.routing_enabled ? "ON" : "OFF"}</div>
              <div
                style={{
                  maxWidth: "100%",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
                title={`BUILD: ${statusData.version || AWAITING}`}
              >
                BUILD: {statusData.version || AWAITING}
              </div>
            </div>
          </div>
        </nav>

        {/* --- MAIN --- */}
        <main style={{ flex: 1, padding: 16, overflow: "auto", maxHeight: "calc(100vh - 52px)" }}>
          {renderPage()}
        </main>
      </div>
    </div>
  );
}



