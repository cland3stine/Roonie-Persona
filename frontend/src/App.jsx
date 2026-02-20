import { useState, useEffect, useRef } from "react";

// NAV
const PAGES_TOP = [
  { id: "live", label: "LIVE" },
  { id: "announcements", label: "ANNOUNCE & EVENTS" },
  { id: "snapshot", label: "CULTURAL SNAPSHOT" },
  { id: "library", label: "LIBRARY INDEX" },
  { id: "culture", label: "CULTURAL NOTES" },
  { id: "innercircle", label: "INNER CIRCLE" },
  { id: "nowplaying", label: "NOW PLAYING" },
];

const PAGES_BOTTOM = [
  { id: "logs", label: "LOGS & REVIEW" },
  { id: "providers", label: "PROVIDERS & COST" },
  { id: "studio", label: "STUDIO PROFILE" },
  { id: "auth", label: "AUTH & ACCOUNTS" },
  { id: "senses", label: "SENSES" },
  { id: "governance", label: "SETTINGS & GOV" },
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
  kill_switch_on: false,
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
  can_post: false,
  blocked_by: [],
  active_director: "ProviderDirector",
  routing_enabled: false,
  eventsub_connected: false,
  eventsub_session_id: null,
  eventsub_last_message_ts: null,
  eventsub_reconnect_count: 0,
  eventsub_last_error: null,
  session_id: null,
  send_fail_count: 0,
  send_fail_reason: null,
  send_fail_at: null,
};

const AWAITING = "\u2014";
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
    fontFamily: "'JetBrains Mono', monospace",
    lineHeight: 1.5,
    fontWeight: 400,
    letterSpacing: 0.5,
  },
};

function AwaitingInline({ style = {}, message }) {
  return <span style={{ ...TEXT_STYLES.muted, ...style }}>{message || AWAITING}</span>;
}

function AwaitingBlock({ style = {}, message }) {
  return <div style={{ ...TEXT_STYLES.muted, ...style }}>{message || AWAITING}</div>;
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

function MessageBlock({ event }) {
  if (!event) return null;
  const user = (event.user_handle || "viewer").trim();
  const msg = (event.message_text || "").trim();
  const response = (event.final_text || event.decision || "").trim();
  const model = (event.model_used || "").trim();
  const category = (event.behavior_category || "").trim().toUpperCase();
  const dtype = (event.decision_type || "speak").trim();
  const suppressionReason = (event.suppression_reason || "").trim();
  const suppressionDetail = (event.suppression_detail || "").trim();

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      {msg && (
        <div style={{ fontSize: 12, color: "#aaa", fontFamily: "'IBM Plex Sans', sans-serif", lineHeight: 1.5 }}>
          <span style={{ color: "#7faacc" }}>@{user}:</span> {msg}
        </div>
      )}
      {dtype === "speak" && response && response.toUpperCase() !== "NOOP" ? (
        <div style={{ fontSize: 12, color: "#ccc", fontFamily: "'IBM Plex Sans', sans-serif", lineHeight: 1.5 }}>
          <span style={{ color: "#2ecc40", fontWeight: 600 }}>Roonie:</span> {response}
        </div>
      ) : dtype === "suppress" ? (
        <div style={{ fontSize: 12, color: "#ff4136", fontFamily: "'IBM Plex Sans', sans-serif", lineHeight: 1.5 }}>
          Suppressed: {suppressionReason}{suppressionDetail ? ` (${suppressionDetail})` : ""}
        </div>
      ) : dtype === "noop" ? (
        <div style={{ fontSize: 12, color: "#ff851b", fontFamily: "'IBM Plex Sans', sans-serif", lineHeight: 1.5 }}>
          NOOP{suppressionReason ? `: ${suppressionReason}` : ""}
        </div>
      ) : (
        <div style={{ fontSize: 12, color: "#666", fontFamily: "'IBM Plex Sans', sans-serif", lineHeight: 1.5 }}>
          No response
        </div>
      )}
      {(model || category) && (
        <div style={{ fontSize: 9, color: "#555", fontFamily: "'JetBrains Mono', monospace", letterSpacing: 0.5 }}>
          {model ? `MODEL: ${model}` : ""}{model && category ? " | " : ""}{category || ""}
        </div>
      )}
    </div>
  );
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

function RackLabel({ children, style = {} }) {
  return (
    <div style={{
      fontSize: 9, letterSpacing: 2.5, color: "#5a5a5a", textTransform: "uppercase",
      fontFamily: "'JetBrains Mono', monospace", fontWeight: 600, marginBottom: 6, userSelect: "none",
      ...style,
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

function BigButton({ label, color = "#ff4136", onClick, active = false, disabled = false, style = {} }) {
  const [pressed, setPressed] = useState(false);
  return (
    <button onClick={disabled ? undefined : onClick} onMouseDown={() => !disabled && setPressed(true)} onMouseUp={() => setPressed(false)} onMouseLeave={() => setPressed(false)}
      style={{
        background: active ? color : "#2a2a2e", color: active ? "#fff" : "#888",
        border: `2px solid ${active ? color : "#3a3a3e"}`, borderRadius: 4,
        padding: "14px 28px", fontSize: 13, fontWeight: 700, letterSpacing: 2,
        fontFamily: "'JetBrains Mono', monospace", cursor: disabled ? "not-allowed" : "pointer", textTransform: "uppercase",
        transition: "all 0.1s ease", transform: pressed && !disabled ? "scale(0.97)" : "scale(1)",
        boxShadow: active ? `0 0 20px ${color}44, inset 0 1px 0 ${color}66` : "inset 0 1px 0 #3a3a3e",
        opacity: disabled ? 0.5 : 1,
        ...style,
      }}>{label}</button>
  );
}

function Toggle({ on, onToggle, disabled = false }) {
  return (
    <button onClick={disabled ? undefined : onToggle} style={{
      display: "flex", alignItems: "center", gap: 10, background: "none",
      border: "1px solid #333", borderRadius: 3, padding: "8px 14px",
      cursor: disabled ? "not-allowed" : "pointer", opacity: disabled ? 0.5 : 1,
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
      <form onSubmit={(e) => { e.preventDefault(); if (!busy && username && password) onSubmit(username, password, rememberLogin); }} style={{
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
          autoComplete="username"
          style={{
            width: "100%", marginBottom: 8, background: "#101015", border: "1px solid #2a2a2e", color: "#ccc",
            borderRadius: 2, padding: "8px 10px", fontSize: 12, fontFamily: "'JetBrains Mono', monospace",
            outline: "none", boxSizing: "border-box",
          }}
        />
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="password"
          autoComplete="current-password"
          style={{
            width: "100%", marginBottom: 10, background: "#101015", border: "1px solid #2a2a2e", color: "#ccc",
            borderRadius: 2, padding: "8px 10px", fontSize: 12, fontFamily: "'JetBrains Mono', monospace",
            outline: "none", boxSizing: "border-box",
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
          type="submit"
          disabled={busy || !username || !password}
          style={{
            width: "100%", background: "#2a2a2e", color: "#ccc", border: "1px solid #3a3a3e", borderRadius: 2,
            padding: "8px 10px", cursor: busy ? "not-allowed" : "pointer",
            fontSize: 11, letterSpacing: 1.4, fontFamily: "'JetBrains Mono', monospace", opacity: busy ? 0.6 : 1,
          }}
        >
          {busy ? "SIGNING IN..." : "SIGN IN"}
        </button>
      </form>
    </div>
  );
}

function useDashboardData(activePage) {
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
  const [innerCircleData, setInnerCircleData] = useState(null);
  const [twitchStatusData, setTwitchStatusData] = useState(null);
  const [twitchNotice, setTwitchNotice] = useState("");
  const refreshDataInFlightRef = useRef(false);
  const [busyAction, setBusyAction] = useState(null);

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
      const [authRes, statusRes, eventsRes, suppressionsRes, operatorRes, queueRes, studioProfileRes, libraryStatusRes, logsEventsRes, logsSuppressionsRes, logsOperatorRes, providersStatusRes, routingStatusRes, sensesStatusRes, memoryCulturalRes, memoryViewersRes, memoryPendingRes, twitchStatusRes, innerCircleRes] = await Promise.all([
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
        apiFetch(`${API_BASE}/api/inner_circle`),
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
      if (innerCircleRes.ok) {
        setInnerCircleData(await innerCircleRes.json());
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

  const refreshLiveData = async () => {
    try {
      const [eventsRes, suppressionsRes] = await Promise.all([
        apiFetch(`${API_BASE}/api/events?limit=5`),
        apiFetch(`${API_BASE}/api/suppressions?limit=5`),
      ]);
      if (eventsRes.ok) setEventsData(await eventsRes.json());
      if (suppressionsRes.ok) setSuppressionsData(await suppressionsRes.json());
    } catch (_err) {
      // Keep prior data on failure.
    }
  };

  const refreshPageData = async (page) => {
    try {
      const fetches = [];
      const handlers = [];
      if (page === "live" || page === "announcements") {
        fetches.push(apiFetch(`${API_BASE}/api/operator_log?limit=5`));
        handlers.push((res) => res.ok && res.json().then(setOperatorLogData));
        fetches.push(apiFetch(`${API_BASE}/api/queue?limit=25`));
        handlers.push((res) => res.ok && res.json().then(setQueueData));
      }
      if (page === "logs") {
        fetches.push(apiFetch(`${API_BASE}/api/logs/events?limit=100&offset=0`));
        handlers.push(async (res) => { if (res.ok) { const body = await res.json(); setLogsEventsData(Array.isArray(body?.items) ? body.items : []); } });
        fetches.push(apiFetch(`${API_BASE}/api/logs/suppressions?limit=100&offset=0`));
        handlers.push(async (res) => { if (res.ok) { const body = await res.json(); setLogsSuppressionsData(Array.isArray(body?.items) ? body.items : []); } });
        fetches.push(apiFetch(`${API_BASE}/api/logs/operator?limit=100&offset=0`));
        handlers.push(async (res) => { if (res.ok) { const body = await res.json(); setLogsOperatorData(Array.isArray(body?.items) ? body.items : []); } });
      }
      if (page === "providers") {
        fetches.push(apiFetch(`${API_BASE}/api/routing/status`));
        handlers.push((res) => res.ok && res.json().then(setRoutingStatusData));
      }
      if (page === "culture") {
        fetches.push(apiFetch(`${API_BASE}/api/memory/cultural?limit=100&offset=0&active_only=0`));
        handlers.push(async (res) => { if (res.ok) { const body = await res.json(); setCulturalNotesData(Array.isArray(body?.items) ? body.items : []); } });
        fetches.push(apiFetch(`${API_BASE}/api/memory/viewers?limit=100&offset=0&active_only=0`));
        handlers.push(async (res) => { if (res.ok) { const body = await res.json(); setViewerNotesData(Array.isArray(body?.items) ? body.items : []); } });
        fetches.push(apiFetch(`${API_BASE}/api/memory/pending?limit=100&offset=0`));
        handlers.push(async (res) => { if (res.ok) { const body = await res.json(); setMemoryPendingData(Array.isArray(body?.items) ? body.items : []); } });
      }
      if (page === "snapshot") {
        fetches.push(apiFetch(`${API_BASE}/api/logs/events?limit=100&offset=0`));
        handlers.push(async (res) => { if (res.ok) { const body = await res.json(); setLogsEventsData(Array.isArray(body?.items) ? body.items : []); } });
        fetches.push(apiFetch(`${API_BASE}/api/logs/suppressions?limit=100&offset=0`));
        handlers.push(async (res) => { if (res.ok) { const body = await res.json(); setLogsSuppressionsData(Array.isArray(body?.items) ? body.items : []); } });
      }
      if (page === "library") {
        fetches.push(apiFetch(`${API_BASE}/api/library_index/status`));
        handlers.push((res) => res.ok && res.json().then(setLibraryStatusData));
      }
      if (page === "studio") {
        fetches.push(apiFetch(`${API_BASE}/api/studio_profile`));
        handlers.push((res) => res.ok && res.json().then(setStudioProfileData));
      }
      if (page === "senses") {
        fetches.push(apiFetch(`${API_BASE}/api/senses/status`));
        handlers.push((res) => res.ok && res.json().then(setSensesStatusData));
      }
      if (page === "innercircle") {
        fetches.push(apiFetch(`${API_BASE}/api/inner_circle`));
        handlers.push((res) => res.ok && res.json().then(setInnerCircleData));
      }
      if (fetches.length) {
        const results = await Promise.all(fetches);
        await Promise.all(results.map((res, i) => handlers[i](res)));
      }
    } catch (_err) {
      // Keep prior data on failure.
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

  const performAction = async (path, payload = {}, actionKey = null) => {
    if (actionKey) setBusyAction(actionKey);
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
      setBusyAction(null);
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

  const saveInnerCircle = async (payload) => {
    const headers = buildOperatorHeaders({ json: true });
    try {
      const response = await apiFetch(`${API_BASE}/api/inner_circle`, {
        method: "PUT",
        headers,
        body: JSON.stringify(payload || {}),
      });
      if (!response.ok) {
        let detail = response.statusText;
        try {
          const body = await response.json();
          detail = body.detail || body.error || detail;
        } catch (_err) {
          // Keep status text fallback.
        }
        console.error(`[Dashboard] inner_circle save failed (${response.status}) ${detail}`);
      } else {
        const body = await response.json();
        if (body && body.inner_circle) setInnerCircleData(body.inner_circle);
      }
    } catch (err) {
      console.error("[Dashboard] inner_circle save error", err);
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
      void refreshTwitchStatus();
      return { ok: success, body };
    } catch (err) {
      setTwitchNotice(String(err || "Unable to start Twitch auth."));
      void refreshTwitchStatus();
      return { ok: false, body: { detail: String(err || "connect error") } };
    }
  };

  const twitchConnectPoll = async (account) => {
    const headers = buildOperatorHeaders({ json: true });
    const payload = { account: String(account || "bot").toLowerCase() };
    try {
      const response = await apiFetch(`${API_BASE}/api/twitch/connect_poll`, {
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
        setTwitchNotice(String(body?.detail || body?.error || "Unable to poll Twitch device auth."));
      }
      void refreshTwitchStatus();
      return { ok: success, body };
    } catch (err) {
      setTwitchNotice(String(err || "Unable to poll Twitch device auth."));
      void refreshTwitchStatus();
      return { ok: false, body: { detail: String(err || "poll error") } };
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
    refreshCoreData();
    refreshLiveData();
    refreshPageData(activePage);
    if (activePage === "providers") refreshSystemHealth();

    const coreInterval = setInterval(refreshCoreData, 2500);
    const liveInterval = setInterval(refreshLiveData, 2500);
    const pageInterval = setInterval(() => refreshPageData(activePage), 15000);
    const healthInterval = setInterval(() => {
      if (activePage === "providers") refreshSystemHealth();
    }, 8000);
    return () => {
      clearInterval(coreInterval);
      clearInterval(liveInterval);
      clearInterval(pageInterval);
      clearInterval(healthInterval);
    };
  }, [activePage]);

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
    innerCircleData,
    twitchStatusData,
    twitchNotice,
    setTwitchNotice,
    performAction,
    busyAction,
    saveStudioProfile,
    saveInnerCircle,
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
    twitchConnectPoll,
    twitchDisconnect,
    fetchChannelEmotes: async () => {
      try {
        const res = await apiFetch(`${API_BASE}/api/twitch/channel_emotes`);
        if (!res.ok) return { ok: false, error: `HTTP ${res.status}` };
        return await res.json();
      } catch (err) {
        return { ok: false, error: String(err || "fetch error") };
      }
    },
  };
}

// --- PAGE: LIVE ---

function LivePage({ statusData, eventsData, suppressionsData, performAction, busyAction }) {
  const status = statusData.kill_switch_on ? "SILENCED"
    : statusData.silenced ? "SILENCED"
    : (statusData.armed ? "ACTIVE" : "INACTIVE");
  const autoNext = eventsData.length ? buildMessageLine(eventsData[0]) : "No pending messages";
  const suppression = suppressionsData.length ? suppressionsData[0] : null;
  const silenceUntilMs = statusData.silence_until ? Date.parse(statusData.silence_until) : NaN;
  const silenceTimer = Number.isFinite(silenceUntilMs)
    ? Math.max(0, Math.ceil((silenceUntilMs - Date.now()) / 1000))
    : null;
  // Chat activity based on messages in the last 5 minutes
  const fiveMinAgo = Date.now() - 5 * 60 * 1000;
  const recentCount = eventsData.filter((e) => e.ts && Date.parse(e.ts) > fiveMinAgo).length;
  const chatActivity = recentCount >= 10 ? "BUSY" : (recentCount >= 5 ? "FLOWING" : "QUIET");
  const contextText = statusData.context_last_active
    ? `${statusData.context_last_turns_used}-turn carry active`
    : "No context carry";
  const activityColors = { QUIET: "#555", FLOWING: "#2ecc40", BUSY: "#ff851b" };
  const handleSilence = () => performAction("/api/live/silence_now", { ttl_seconds: 300 }, "silence");
  const handleToggle = () => performAction(statusData.armed ? "/api/live/disarm" : "/api/live/arm", {}, "arm");
  const handleEstop = () => {
    if (statusData.kill_switch_on) {
      if (window.confirm("Release Emergency Stop? System will remain DISARMED.")) {
        performAction("/api/live/kill_switch_release", {}, "estop");
      }
    } else {
      performAction("/api/live/emergency_stop", {}, "estop");
    }
  };

  const systemLive = statusData.armed && !statusData.read_only_mode && !statusData.silenced && !statusData.kill_switch_on && statusData.can_post;
  const blockedReason = statusData.kill_switch_on ? "KILL_SWITCH" : (Array.isArray(statusData.blocked_by) && statusData.blocked_by.length ? statusData.blocked_by[0] : "UNKNOWN");
  const isOfflineDirector = statusData.active_director === "OfflineDirector";

  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, gridTemplateRows: "auto auto auto 1fr" }}>
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
          <BigButton
            label={busyAction === "estop" ? (statusData.kill_switch_on ? "E-STOP ENGAGED..." : "EMERGENCY STOP...") : (statusData.kill_switch_on ? "E-STOP ENGAGED" : "EMERGENCY STOP")}
            color="#ff4136"
            active={statusData.kill_switch_on}
            disabled={busyAction === "estop"}
            onClick={handleEstop}
            style={{ padding: "16px 32px", fontSize: 14 }}
          />
          <BigButton label={busyAction === "silence" ? "SILENCE NOW..." : "SILENCE NOW"} color="#ff4136" active={status === "SILENCED"} disabled={busyAction === "silence"} onClick={handleSilence} style={{ padding: "16px 32px", fontSize: 14 }} />
          <Toggle on={statusData.armed} onToggle={handleToggle} disabled={busyAction === "arm"} />
        </div>
      </div>

      <RackPanel style={{ gridColumn: "1 / -1", padding: "10px 16px" }}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 20, alignItems: "center", fontFamily: "'JetBrains Mono', monospace", fontSize: 11, letterSpacing: 1 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <Led color={systemLive ? "#2ecc40" : "#ff4136"} size={8} pulse={systemLive} />
            <span style={{ color: systemLive ? "#2ecc40" : "#ff4136", fontWeight: 700 }}>{systemLive ? "SYSTEM LIVE" : `BLOCKED: ${blockedReason}`}</span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <Led color={statusData.read_only_mode ? "#ff4136" : "#2ecc40"} size={8} />
            <span style={{ color: statusData.read_only_mode ? "#ff4136" : "#888" }}>DRY_RUN {statusData.read_only_mode ? "ON" : "OFF"}</span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <Led color={statusData.twitch_connected ? "#2ecc40" : "#ff4136"} size={8} />
            <span style={{ color: statusData.twitch_connected ? "#888" : "#ff4136" }}>TWITCH {statusData.twitch_connected ? "CONNECTED" : "DISCONNECTED"}</span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <Led color={statusData.eventsub_connected ? "#2ecc40" : "#ff4136"} size={8} />
            <span style={{ color: statusData.eventsub_connected ? "#888" : "#ff4136" }}>EVENTSUB {statusData.eventsub_connected ? "OK" : "DOWN"}</span>
          </div>
          {statusData.send_fail_count > 0 && (
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <Led color="#ff4136" size={8} pulse />
              <span style={{ color: "#ff4136", fontWeight: 600 }}>SEND FAIL</span>
              <span style={{ color: "#ff4136", fontSize: 9 }}>({statusData.send_fail_count}x)</span>
            </div>
          )}
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <Led color={isOfflineDirector ? "#ff4136" : "#2ecc40"} size={8} pulse={isOfflineDirector} />
            <span style={{ color: isOfflineDirector ? "#ff4136" : "#888" }}>{isOfflineDirector ? "\u26A0 OFFLINE" : "PROVIDER"}</span>
          </div>
          <div style={{ width: 1, height: 14, background: "#333", flexShrink: 0 }} />
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <Led color={activityColors[chatActivity]} size={8} pulse={chatActivity === "BUSY"} />
            <span style={{ color: activityColors[chatActivity], fontWeight: chatActivity !== "QUIET" ? 600 : 400 }}>CHAT: {chatActivity}</span>
            <span style={{ color: "#555", fontSize: 9 }}>({recentCount}/5m)</span>
          </div>
        </div>
        {((!statusData.eventsub_connected && statusData.eventsub_last_error) || (statusData.send_fail_count > 0 && statusData.send_fail_reason)) && (
          <div style={{ marginTop: 6, fontSize: 9, color: "#666", fontFamily: "'JetBrains Mono', monospace", lineHeight: 1.6, wordBreak: "break-word" }}>
            {!statusData.eventsub_connected && statusData.eventsub_last_error && (
              <div>EVENTSUB: {String(statusData.eventsub_last_error)}</div>
            )}
            {statusData.send_fail_count > 0 && statusData.send_fail_reason && (
              <div>SEND FAIL: {String(statusData.send_fail_reason)}</div>
            )}
          </div>
        )}
      </RackPanel>

      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <RackPanel>
          <RackLabel>Next (Auto-Approved)</RackLabel>
          {status === "ACTIVE" ? (
            <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
              <Led color="#2ecc40" size={6} pulse />
              <div style={{ fontSize: 13, color: "#ccc", lineHeight: 1.5, fontFamily: "'IBM Plex Sans', sans-serif" }}>{autoNext}</div>
            </div>
          ) : (
            <AwaitingBlock message="System not active" />
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
                {suppression ? `${suppression.suppression_reason || "SUPPRESSED"}${suppression.suppression_detail ? ` (${suppression.suppression_detail})` : ""}` : "No suppressions yet"}
              </div>
              <div style={{ ...TEXT_STYLES.meta, marginTop: 4 }}>
                RULE: {suppression ? (suppression.suppression_reason || "UNKNOWN") : "\u2014"} - {suppression ? fmtTime(suppression.ts) : "\u2014"}
              </div>
            </div>
          </div>
        </RackPanel>

      </div>

      <RackPanel style={{ display: "flex", flexDirection: "column" }}>
        <RackLabel>Recent Messages</RackLabel>
        <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 2 }}>
          {(eventsData.length ? eventsData : []).map((msg, i) => {
            const dtype = msg.decision_type || "speak";
            const borderColor = dtype === "suppress" ? "#ff413630" : dtype === "noop" ? "#ff851b30" : "#2a2a2e";
            return (
              <div key={i} style={{
                padding: "10px 12px",
                background: i % 2 === 0 ? "#16161a" : "transparent",
                borderLeft: `3px solid ${borderColor}`,
                borderRadius: "0 2px 2px 0",
                display: "flex", gap: 12, alignItems: "flex-start",
              }}>
                <Timestamp time={fmtTime(msg.ts)} />
                <MessageBlock event={msg} />
              </div>
            );
          })}
          {!eventsData.length && <AwaitingBlock style={{ padding: "10px 0" }} message="No messages yet" />}
        </div>
      </RackPanel>
    </div>
  );
}
function StudioProfilePage({ studioProfileData, saveStudioProfile, fetchChannelEmotes }) {
  const [draft, setDraft] = useState(null);
  const [emotePullStatus, setEmotePullStatus] = useState(null);
  const [emotesExpanded, setEmotesExpanded] = useState(false);
  const [editing, setEditing] = useState(null);
  const [draftGear, setDraftGear] = useState([]);
  const [draftSocial, setDraftSocial] = useState([]);
  const [draftFaq, setDraftFaq] = useState([]);

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

  const startEditing = (section) => {
    if (section === "gear") setDraftGear([...gearItems]);
    if (section === "social") setDraftSocial((profile.social_links || []).map((l) => ({ ...l })));
    if (section === "faq") setDraftFaq((profile.faq || []).map((f) => ({ ...f })));
    setEditing(section);
  };

  const cancelEditing = () => setEditing(null);

  const saveGear = () => {
    const cleaned = draftGear.map((s) => s.trim()).filter(Boolean);
    const next = { ...profile, gear: cleaned };
    updateDraft(next);
    saveStudioProfile(next, "PUT");
    setEditing(null);
  };

  const saveSocial = () => {
    const cleaned = draftSocial.filter((l) => l.label.trim() && l.url.trim()).map((l) => ({ label: l.label.trim(), url: l.url.trim() }));
    const next = { ...profile, social_links: cleaned };
    updateDraft(next);
    saveStudioProfile(next, "PUT");
    setEditing(null);
  };

  const saveFaqDraft = () => {
    const cleaned = draftFaq.filter((f) => f.q.trim() && f.a.trim()).map((f) => ({ q: f.q.trim(), a: f.a.trim() }));
    const next = { ...profile, faq: cleaned };
    updateDraft(next);
    saveStudioProfile(next, "PUT");
    setEditing(null);
  };

  const inlineInputStyle = { background: "#111114", border: "1px solid #2a2a2e", borderRadius: 2, padding: "4px 8px", color: "#aaa", fontSize: 11, fontFamily: "'IBM Plex Sans', sans-serif", outline: "none", width: "100%", boxSizing: "border-box" };
  const editBtnStyle = { background: "transparent", border: "1px dashed #333", color: "#555", padding: "8px 16px", fontSize: 10, letterSpacing: 1.5, fontFamily: "'JetBrains Mono', monospace", cursor: "pointer", borderRadius: 2, width: "100%" };
  const saveBtnStyle = { ...editBtnStyle, border: "1px solid #2ecc4066", color: "#2ecc40" };
  const cancelBtnStyle = { ...editBtnStyle };

  const [twitchEmoteImages, setTwitchEmoteImages] = useState({});

  const normalizeEmote = (item) => {
    if (typeof item === "string") return { name: item.trim(), desc: "", denied: false };
    if (item && typeof item === "object") return { name: String(item.name || "").trim(), desc: String(item.desc || "").trim(), denied: Boolean(item.denied) };
    return null;
  };

  const emoteList = (profile.approved_emotes || []).map(normalizeEmote).filter((e) => e && e.name);
  const approvedEmotes = emoteList.filter((e) => !e.denied);
  const deniedEmotes = emoteList.filter((e) => e.denied);

  const updateEmote = (name, patch) => {
    const next = emoteList.map((e) => e.name === name ? { ...e, ...patch } : e);
    const updated = { ...profile, approved_emotes: next };
    updateDraft(updated);
    saveStudioProfile(updated, "PUT");
  };

  const pullEmotesFromTwitch = async () => {
    if (!fetchChannelEmotes) return;
    setEmotePullStatus("loading");
    const result = await fetchChannelEmotes();
    if (!result.ok) {
      setEmotePullStatus(result.error || "Error fetching emotes");
      return;
    }
    const fetched = Array.isArray(result.emotes) ? result.emotes : [];
    const imageMap = { ...twitchEmoteImages };
    fetched.forEach((e) => { if (e.name && e.url) imageMap[e.name] = e.url; });
    setTwitchEmoteImages(imageMap);
    const existingNames = new Set(emoteList.map((e) => e.name));
    const newEmotes = fetched.filter((e) => e.name && !existingNames.has(e.name)).map((e) => ({ name: e.name, desc: "", denied: false }));
    const merged = [...emoteList, ...newEmotes];
    const updated = { ...profile, approved_emotes: merged };
    updateDraft(updated);
    saveStudioProfile(updated, "PUT");
    setEmotePullStatus(newEmotes.length > 0 ? `+${newEmotes.length} new emote${newEmotes.length !== 1 ? "s" : ""} added & saved` : "No new emotes found");
  };

  return (
    <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 12 }}>
      <RackPanel>
        <RackLabel>Studio Gear List</RackLabel>
        {editing === "gear" ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {draftGear.map((item, i) => (
              <div key={i} style={{ display: "flex", gap: 6, alignItems: "center" }}>
                <input type="text" value={item} onChange={(e) => { const next = [...draftGear]; next[i] = e.target.value; setDraftGear(next); }} style={inlineInputStyle} />
                <span onClick={() => { const next = [...draftGear]; next.splice(i, 1); setDraftGear(next); }} style={{ color: "#ff4136", fontSize: 13, cursor: "pointer", fontFamily: "'JetBrains Mono', monospace", padding: "0 4px", flexShrink: 0 }}>&times;</span>
              </div>
            ))}
            <div onClick={() => setDraftGear([...draftGear, ""])} style={{ fontSize: 10, color: "#555", letterSpacing: 1, fontFamily: "'JetBrains Mono', monospace", cursor: "pointer", marginTop: 4 }}>+ ADD ITEM</div>
            <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
              <button onClick={saveGear} style={saveBtnStyle}>SAVE</button>
              <button onClick={cancelEditing} style={cancelBtnStyle}>CANCEL</button>
            </div>
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            {gearItems.map((item, i) => (
              <div key={i} style={{ padding: "6px 10px", fontSize: 12, color: "#aaa", fontFamily: "'IBM Plex Sans', sans-serif", borderLeft: "2px solid #2a2a2e", marginBottom: 2 }}>
                {item}
              </div>
            ))}
            {!gearItems.length ? <AwaitingBlock style={{ padding: "6px 10px" }} message="No gear items added" /> : null}
            <button onClick={() => startEditing("gear")} style={editBtnStyle}>EDIT STUDIO GEAR</button>
          </div>
        )}
      </RackPanel>
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <RackPanel><RackLabel>Safe Location</RackLabel><div onClick={editLocation} style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}><Led color="#2ecc40" size={6} /><span style={{ fontSize: 13, color: "#ccc", fontFamily: "'IBM Plex Sans', sans-serif" }}>{profile.location?.display || "No location set"}</span></div><div style={{ fontSize: 10, color: "#444", fontFamily: "'JetBrains Mono', monospace", marginTop: 6 }}>LOCKED - Roonie will only reference this location</div></RackPanel>
        <RackPanel><RackLabel>Social Links</RackLabel>
          {editing === "social" ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {draftSocial.map((link, i) => (
                <div key={i} style={{ display: "flex", gap: 6, alignItems: "center" }}>
                  <input type="text" value={link.label} onChange={(e) => { const next = [...draftSocial]; next[i] = { ...next[i], label: e.target.value }; setDraftSocial(next); }} placeholder="Label" style={{ ...inlineInputStyle, width: "35%" }} />
                  <input type="text" value={link.url} onChange={(e) => { const next = [...draftSocial]; next[i] = { ...next[i], url: e.target.value }; setDraftSocial(next); }} placeholder="URL" style={{ ...inlineInputStyle, flex: 1 }} />
                  <span onClick={() => { const next = [...draftSocial]; next.splice(i, 1); setDraftSocial(next); }} style={{ color: "#ff4136", fontSize: 13, cursor: "pointer", fontFamily: "'JetBrains Mono', monospace", padding: "0 4px", flexShrink: 0 }}>&times;</span>
                </div>
              ))}
              <div onClick={() => setDraftSocial([...draftSocial, { label: "", url: "" }])} style={{ fontSize: 10, color: "#555", letterSpacing: 1, fontFamily: "'JetBrains Mono', monospace", cursor: "pointer", marginTop: 4 }}>+ ADD LINK</div>
              <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
                <button onClick={saveSocial} style={saveBtnStyle}>SAVE</button>
                <button onClick={cancelEditing} style={cancelBtnStyle}>CANCEL</button>
              </div>
            </div>
          ) : (
            <div>
              {(profile.social_links || []).map((link, i) => (<div key={i} style={{ padding: "6px 0", fontSize: 12, color: "#7faacc", fontFamily: "'JetBrains Mono', monospace", borderBottom: "1px solid #222" }}>{link.label}: {link.url}</div>))}
              {!(profile.social_links || []).length ? <div style={{ padding: "6px 0", fontSize: 11, color: "#666", fontFamily: "'IBM Plex Sans', sans-serif" }}>No social links added</div> : null}
              <button onClick={() => startEditing("social")} style={{ ...editBtnStyle, marginTop: 8 }}>EDIT SOCIAL LINKS</button>
            </div>
          )}
        </RackPanel>
        <RackPanel>
          <div onClick={() => setEmotesExpanded(!emotesExpanded)} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", cursor: "pointer", marginBottom: emotesExpanded ? 10 : 0 }}>
            <RackLabel style={{ marginBottom: 0 }}>Channel Emotes {emoteList.length ? <span style={{ fontSize: 9, color: "#555", fontFamily: "'JetBrains Mono', monospace", marginLeft: 6 }}>({emoteList.length})</span> : null}</RackLabel>
            <span style={{ fontSize: 10, color: "#555", fontFamily: "'JetBrains Mono', monospace", transition: "transform 0.15s", transform: emotesExpanded ? "rotate(180deg)" : "rotate(0deg)" }}>&#9660;</span>
          </div>
          {emotesExpanded && (<>
            <button onClick={pullEmotesFromTwitch} disabled={emotePullStatus === "loading"} style={{ marginBottom: 10, background: "transparent", border: "1px dashed #333", color: "#555", padding: "8px 16px", fontSize: 10, letterSpacing: 1.5, fontFamily: "'JetBrains Mono', monospace", cursor: emotePullStatus === "loading" ? "wait" : "pointer", borderRadius: 2, width: "100%" }}>{emotePullStatus === "loading" ? "PULLING..." : "PULL FROM TWITCH"}</button>
            {emotePullStatus && emotePullStatus !== "loading" ? <div style={{ marginBottom: 8, fontSize: 10, color: "#888", fontFamily: "'IBM Plex Sans', sans-serif" }}>{emotePullStatus}</div> : null}
            {!emoteList.length ? <div style={{ padding: "6px 0", fontSize: 11, color: "#666", fontFamily: "'IBM Plex Sans', sans-serif" }}>No emotes loaded</div> : null}
            <div style={{ maxHeight: 350, overflowY: "auto" }}>
              {[...approvedEmotes, ...deniedEmotes].map((emote) => {
                const isDenied = emote.denied;
                const imgUrl = twitchEmoteImages[emote.name];
                return (
                  <div key={emote.name} style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 8px", marginBottom: 4, background: "#15151a", borderRadius: 3, border: "1px solid #222", opacity: isDenied ? 0.4 : 1 }}>
                    {imgUrl ? <img src={imgUrl} alt={emote.name} style={{ width: 28, height: 28, imageRendering: "pixelated" }} /> : <div style={{ width: 28, height: 28, background: "#222", borderRadius: 2, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 10, color: "#555" }}>?</div>}
                    <span style={{ fontSize: 12, color: "#ccc", fontFamily: "'JetBrains Mono', monospace", minWidth: 80 }}>{emote.name}</span>
                    <input type="text" value={emote.desc} onChange={(e) => updateEmote(emote.name, { desc: e.target.value })} placeholder="describe when to use..." style={{ flex: 1, background: "#111114", border: "1px solid #2a2a2e", borderRadius: 2, padding: "4px 8px", color: "#aaa", fontSize: 11, fontFamily: "'IBM Plex Sans', sans-serif", outline: "none" }} />
                    {isDenied ? (
                      <button onClick={() => updateEmote(emote.name, { denied: false })} style={{ background: "transparent", border: "1px solid #2ecc4044", color: "#2ecc40", padding: "3px 10px", fontSize: 9, letterSpacing: 1, fontFamily: "'JetBrains Mono', monospace", cursor: "pointer", borderRadius: 2, whiteSpace: "nowrap" }}>APPROVE</button>
                    ) : (<>
                      <button onClick={() => updateEmote(emote.name, { denied: false })} style={{ background: "transparent", border: "1px solid #2ecc4044", color: "#2ecc40", padding: "3px 10px", fontSize: 9, letterSpacing: 1, fontFamily: "'JetBrains Mono', monospace", cursor: "pointer", borderRadius: 2, opacity: 0.5 }}>APPROVED</button>
                      <button onClick={() => updateEmote(emote.name, { denied: true })} style={{ background: "transparent", border: "1px solid #ff851b44", color: "#ff851b", padding: "3px 10px", fontSize: 9, letterSpacing: 1, fontFamily: "'JetBrains Mono', monospace", cursor: "pointer", borderRadius: 2 }}>DENY</button>
                    </>)}
                  </div>
                );
              })}
            </div>
          </>)}
        </RackPanel>
        <RackPanel><RackLabel>FAQ Short Answers</RackLabel>
          {editing === "faq" ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {draftFaq.map((faq, i) => (
                <div key={i} style={{ display: "flex", gap: 6, alignItems: "flex-start" }}>
                  <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 4 }}>
                    <input type="text" value={faq.q} onChange={(e) => { const next = [...draftFaq]; next[i] = { ...next[i], q: e.target.value }; setDraftFaq(next); }} placeholder="Question" style={inlineInputStyle} />
                    <input type="text" value={faq.a} onChange={(e) => { const next = [...draftFaq]; next[i] = { ...next[i], a: e.target.value }; setDraftFaq(next); }} placeholder="Answer" style={inlineInputStyle} />
                  </div>
                  <span onClick={() => { const next = [...draftFaq]; next.splice(i, 1); setDraftFaq(next); }} style={{ color: "#ff4136", fontSize: 13, cursor: "pointer", fontFamily: "'JetBrains Mono', monospace", padding: "4px 4px 0", flexShrink: 0 }}>&times;</span>
                </div>
              ))}
              <div onClick={() => setDraftFaq([...draftFaq, { q: "", a: "" }])} style={{ fontSize: 10, color: "#555", letterSpacing: 1, fontFamily: "'JetBrains Mono', monospace", cursor: "pointer", marginTop: 4 }}>+ ADD FAQ</div>
              <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
                <button onClick={saveFaqDraft} style={saveBtnStyle}>SAVE</button>
                <button onClick={cancelEditing} style={cancelBtnStyle}>CANCEL</button>
              </div>
            </div>
          ) : (
            <div>
              {(profile.faq || []).map((faq, i) => (<div key={i} style={{ marginBottom: 10 }}><div style={{ fontSize: 11, color: "#888", fontFamily: "'IBM Plex Sans', sans-serif", fontWeight: 600 }}>{faq.q}</div><div style={{ fontSize: 12, color: "#aaa", fontFamily: "'IBM Plex Sans', sans-serif", marginTop: 2, paddingLeft: 8, borderLeft: "2px solid #333" }}>{faq.a}</div></div>))}
              {!(profile.faq || []).length ? <div style={{ padding: "6px 0", fontSize: 11, color: "#666", fontFamily: "'IBM Plex Sans', sans-serif" }}>No FAQ entries added</div> : null}
              <button onClick={() => startEditing("faq")} style={{ ...editBtnStyle, marginTop: 8 }}>EDIT FAQ</button>
            </div>
          )}
        </RackPanel>
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
    : "0";
  const lastUpdatedText = libraryStatusData?.last_indexed_at
    ? new Date(libraryStatusData.last_indexed_at).toLocaleDateString("en-US")
    : "Not indexed";

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
            <div style={{ fontSize: 10, color: "#444", marginTop: 4, fontFamily: "'IBM Plex Sans', sans-serif" }}>{uploadLabel || "or click to browse"}</div>
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
        {sq && f.length === 0 && <AwaitingBlock style={{ padding: "8px 4px", borderTop: "1px solid #1f1f22" }} message="No matching tracks" />}
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
        {operatorMsg ? (<><div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}><Led color="#ff851b" size={6} /><div style={{ fontSize: 13, color: "#ccc", lineHeight: 1.5, fontFamily: "'IBM Plex Sans', sans-serif" }}>"{operatorMsg}"</div></div><div style={{ marginTop: 10, display: "flex", gap: 8 }}><RackButton label="NOT AVAILABLE" color="#7faacc" disabled /><button onClick={() => { if (queueId) performAction("/api/queue/cancel", { id: queueId }); }} style={{ background: "transparent", color: "#ff4136", border: "1px solid #ff413644", borderRadius: 2, padding: "5px 14px", fontSize: 10, fontWeight: 700, letterSpacing: 1.5, cursor: "pointer", fontFamily: "'JetBrains Mono', monospace" }}>CANCEL</button></div></>) : (<AwaitingBlock message="Queue empty" />)}
        <div style={{ fontSize: 9, color: "#444", fontFamily: "'JetBrains Mono', monospace", marginTop: 10, letterSpacing: 1 }}>MANUAL ONLY - never auto-fires - no timed automation</div>
      </RackPanel>
      <RackPanel>
        <RackLabel>Upcoming Events</RackLabel>
        <div style={{ fontSize: 10, color: "#555", fontFamily: "'IBM Plex Sans', sans-serif", marginBottom: 12 }}>Read-only schedule. Roonie can reference these if asked.</div>
        <div style={{ fontSize: 12, color: "#666", fontFamily: "'IBM Plex Sans', sans-serif" }}>No upcoming events</div>
      </RackPanel>
    </div>
  );
}

// --- PAGE: NOW PLAYING ---

function NowPlayingPage() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        <RackPanel><RackLabel>File Status</RackLabel><div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}><Led color="#ff851b" size={10} pulse={false} /><AwaitingInline style={{ color: "#ff851b", fontWeight: 700 }} message="No track loaded" /></div><RackLabel>Last Updated</RackLabel><AwaitingBlock message="No updates" /><div style={{ marginTop: 12 }}><RackButton label="NOT AVAILABLE" color="#7faacc" disabled /></div></RackPanel>
        <RackPanel><RackLabel>File Source</RackLabel><div style={{ padding: "10px 14px", background: "#111114", border: "1px solid #2a2a2e", borderRadius: 2, wordBreak: "break-all" }}><AwaitingInline message="No source configured" /></div><div style={{ marginTop: 8, fontSize: 10, color: "#555", fontFamily: "'IBM Plex Sans', sans-serif", lineHeight: 1.6 }}>Read-only track source.</div></RackPanel>
      </div>
      <div style={{ background: "#1a1a1e", border: "1px solid #2a2a2e", borderRadius: 3, padding: 16, position: "relative", opacity: 0.35, pointerEvents: "none", userSelect: "none" }}>
        <div style={{ position: "absolute", top: 6, left: 8, width: 4, height: 4, borderRadius: "50%", background: "#252528", border: "1px solid #333" }} /><div style={{ position: "absolute", top: 6, right: 8, width: 4, height: 4, borderRadius: "50%", background: "#252528", border: "1px solid #333" }} />
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}><RackLabel>API Source (Later)</RackLabel><span style={{ fontSize: 9, padding: "3px 8px", background: "#ff413612", border: "1px solid #ff413633", borderRadius: 2, color: "#ff4136", letterSpacing: 1.5, fontFamily: "'JetBrains Mono', monospace", fontWeight: 700 }}>OFF</span></div>
        <AwaitingBlock style={{ marginBottom: 8 }} message="Not connected" />
        <div style={{ borderTop: "1px solid #222", paddingTop: 8 }}><div style={{ fontSize: 9, color: "#444", letterSpacing: 1.5, fontFamily: "'JetBrains Mono', monospace" }}>NOT AVAILABLE</div></div>
      </div>
    </div>
  );
}

// --- PAGE: LOGS & REVIEW ---

function LogsPage({ eventsData, suppressionsData, operatorLogData }) {
  const [at, setAt] = useState("messages");
  const tabs = [{ id: "messages", label: "MESSAGE LOG" }, { id: "operator", label: "OPERATOR LOG" }];

  // Unified event stream: merge events + suppressions, deduplicate by ts+user_handle, sort by time desc
  const allEvents = [...(eventsData || []), ...(suppressionsData || [])];
  const seen = new Set();
  const unified = allEvents.filter((e) => {
    const key = `${e.ts || ""}|${e.user_handle || ""}|${e.message_text || ""}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).sort((a, b) => {
    const ta = a.ts ? new Date(a.ts).getTime() : 0;
    const tb = b.ts ? new Date(b.ts).getTime() : 0;
    return tb - ta;
  });

  const operators = operatorLogData.length ? operatorLogData : [];

  const ledColorForType = (dtype) => {
    if (dtype === "suppress") return "#ff4136";
    if (dtype === "noop") return "#ff851b";
    return "#2ecc40";
  };
  const borderColorForType = (dtype) => {
    if (dtype === "suppress") return "#ff413633";
    if (dtype === "noop") return "#ff851b33";
    return "transparent";
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", gap: 0, borderBottom: "1px solid #2a2a2e" }}>
        {tabs.map((tab) => (
          <button key={tab.id} onClick={() => setAt(tab.id)} style={{
            background: at === tab.id ? "#1a1a1e" : "transparent",
            color: at === tab.id ? "#ccc" : "#555",
            border: "none",
            borderBottom: at === tab.id ? "2px solid #7faacc" : "2px solid transparent",
            padding: "10px 20px", fontSize: 11, letterSpacing: 2,
            fontFamily: "'JetBrains Mono', monospace", fontWeight: 600, cursor: "pointer",
          }}>{tab.label}</button>
        ))}
      </div>
      <RackPanel>
        {at === "messages" && (
          <>
            <RackLabel>Unified Message Log</RackLabel>
            {unified.map((msg, i) => {
              const dtype = msg.decision_type || "speak";
              return (
                <div key={i} style={{
                  display: "flex", gap: 12, padding: "10px 0",
                  borderBottom: "1px solid #1f1f22", alignItems: "flex-start",
                  borderLeft: dtype !== "speak" ? `3px solid ${borderColorForType(dtype)}` : "3px solid transparent",
                  paddingLeft: 8,
                }}>
                  <Timestamp time={fmtTime(msg.ts)} />
                  <Led color={ledColorForType(dtype)} size={5} />
                  <MessageBlock event={msg} />
                </div>
              );
            })}
            {unified.length === 0 && <div style={{ fontSize: 12, color: "#555", fontFamily: "'IBM Plex Sans', sans-serif" }}>No messages logged</div>}
          </>
        )}
        {at === "operator" && (
          <>
            <RackLabel>Operator Action Log</RackLabel>
            {operators.map((e, i) => {
              const opColor = e.operator === "Art" ? "#7faacc" : "#cc7faa";
              const resultColor = e.result && String(e.result).startsWith("DENIED") ? "#ff4136" : "#2ecc40";
              return (
                <div key={i} style={{
                  display: "flex", gap: 12, padding: "10px 0",
                  borderBottom: "1px solid #1f1f22", alignItems: "flex-start", flexWrap: "wrap",
                }}>
                  <Timestamp time={fmtTime(e.ts)} />
                  <span style={{
                    fontSize: 10, padding: "2px 8px",
                    background: `${opColor}18`, border: `1px solid ${opColor}33`,
                    borderRadius: 2, color: opColor,
                    fontFamily: "'JetBrains Mono', monospace", fontWeight: 700,
                    letterSpacing: 1, minWidth: 30, textAlign: "center",
                  }}>
                    {String(e.operator || "").toUpperCase() || "\u2014"}
                    {e.role ? ` (${e.role})` : ""}
                  </span>
                  {e.auth_mode && (
                    <span style={{
                      fontSize: 9, padding: "2px 6px",
                      background: "#22222a", border: "1px solid #333",
                      borderRadius: 2, color: "#666",
                      fontFamily: "'JetBrains Mono', monospace", letterSpacing: 1,
                    }}>{e.auth_mode}</span>
                  )}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <span style={{ fontSize: 12, color: "#aaa", fontFamily: "'IBM Plex Sans', sans-serif" }}>{e.action || "\u2014"}</span>
                      {e.result && (
                        <span style={{
                          fontSize: 9, padding: "1px 6px",
                          background: `${resultColor}12`, border: `1px solid ${resultColor}33`,
                          borderRadius: 2, color: resultColor,
                          fontFamily: "'JetBrains Mono', monospace", fontWeight: 600, letterSpacing: 1,
                        }}>{e.result}</span>
                      )}
                    </div>
                    {e.payload_summary && (
                      <div style={{ fontSize: 9, color: "#555", fontFamily: "'JetBrains Mono', monospace", marginTop: 3 }}>
                        {e.payload_summary}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
            {operators.length === 0 && <div style={{ fontSize: 12, color: "#555", fontFamily: "'IBM Plex Sans', sans-serif" }}>No operator actions logged</div>}
          </>
        )}
      </RackPanel>
    </div>
  );
}
function ProvidersPage({ statusData, providersStatusData, routingStatusData, systemHealthData, readinessData, setProviderActive, setProviderCaps, setRoutingEnabled, setActiveDirector, setDryRunEnabled }) {
  const providerMeta = {
    openai: { name: "OpenAI" },
    grok: { name: "Grok" },
    anthropic: { name: "Anthropic" },
  };
  const providerModels = (providersStatusData && typeof providersStatusData.provider_models === "object" && providersStatusData.provider_models)
    || (statusData && typeof statusData.provider_models === "object" && statusData.provider_models)
    || {};
  const resolvedModels = (providersStatusData && typeof providersStatusData.resolved_models === "object" && providersStatusData.resolved_models)
    || (statusData && typeof statusData.resolved_models === "object" && statusData.resolved_models)
    || {};
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
      model: String(providerModels[id] || "").trim() || AWAITING,
      latency,
      requests: readNumericField(source, "requests"),
      failures: readNumericField(source, "failures"),
      moderationBlocks: readNumericField(source, "moderation_blocks"),
    };
  });
  const ap = String(providersStatusData?.active_provider || "");
  const a = providers.find((p) => p.id === ap) || null;
  const activeModel = String(
    providersStatusData?.active_model
    || statusData?.active_model
    || (a && a.model)
    || ""
  ).trim();
  const openaiModel = String(
    providerModels.openai
    || resolvedModels.openai_model
    || ""
  ).trim() || AWAITING;
  const directorModel = String(
    resolvedModels.director_model
    || providerModels.openai
    || ""
  ).trim() || AWAITING;
  const grokModel = String(
    providerModels.grok
    || resolvedModels.grok_model
    || ""
  ).trim() || AWAITING;
  const usage = providersStatusData?.usage || {};
  const caps = providersStatusData?.caps || {};
  const requestsUsed = hasNumericField(usage, "requests") ? Number(usage.requests) : null;
  const requestsMax = hasNumericField(caps, "daily_requests_max") ? Number(caps.daily_requests_max) : 0;
  const tokensUsed = hasNumericField(usage, "tokens") ? Number(usage.tokens) : null;
  const dailyCostText = tokensUsed !== null && tokensUsed > 0 ? `${tokensUsed}` : "0";
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
  const metaRowStyle = { display: "flex", justifyContent: "space-between", padding: "4px 0", borderBottom: "1px solid #1f1f22" };
  const metaLabelStyle = { fontSize: 10, color: "#555", fontFamily: "'JetBrains Mono', monospace", letterSpacing: 1 };
  const metaValueStyle = { fontSize: 10, color: "#aaa", fontFamily: "'JetBrains Mono', monospace" };
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
      <RackPanel>
        <RackLabel>Active Provider</RackLabel>
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
          <Led color="#2ecc40" size={10} pulse={Boolean(a)} label="ACTIVE" />
          <div>
            <div style={{ fontSize: 18, fontWeight: 700, color: "#ccc", fontFamily: "'JetBrains Mono', monospace" }}>{a?.name || AWAITING}</div>
            <div style={{ fontSize: 10, color: "#666", fontFamily: "'JetBrains Mono', monospace" }}>{activeModel || AWAITING}</div>
          </div>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 16 }}>
          <div>
            <RackLabel>Latency (avg)</RackLabel>
            <div style={{ fontSize: 20, fontWeight: 700, color: "#ccc", fontFamily: "'JetBrains Mono', monospace" }}>{a?.latency !== null && Number.isFinite(a?.latency) ? `${a.latency}ms` : AWAITING}</div>
          </div>
          <div>
            <RackLabel>Failures</RackLabel>
            <div style={{ fontSize: 20, fontWeight: 700, color: "#ccc", fontFamily: "'JetBrains Mono', monospace" }}>{a?.failures !== null && Number.isFinite(a?.failures) ? `${a.failures}` : AWAITING}</div>
          </div>
        </div>
        {a?.latency !== null && Number.isFinite(a?.latency) && <div style={{ marginBottom: 16 }}><MeterBar value={a.latency} max={1000} color={a.latency < 500 ? "#2ecc40" : "#ff851b"} label="Response latency" /></div>}
        <div style={{ display: "flex", flexDirection: "column", gap: 0, background: "#111114", border: "1px solid #1f1f22", borderRadius: 2, padding: "6px 10px" }}>
          <div style={metaRowStyle}><span style={metaLabelStyle}>REQUESTS</span><span style={metaValueStyle}>{a?.requests !== null && Number.isFinite(a?.requests) ? a.requests : AWAITING}</span></div>
          <div style={metaRowStyle}><span style={metaLabelStyle}>MOD BLOCKS</span><span style={metaValueStyle}>{a?.moderationBlocks !== null && Number.isFinite(a?.moderationBlocks) ? a.moderationBlocks : AWAITING}</span></div>
          <div style={metaRowStyle}><span style={metaLabelStyle}>ROUTING</span><span style={metaValueStyle}>{routingEnabled ? "ON" : "OFF"} ({routingOverride})</span></div>
          <div style={metaRowStyle}><span style={metaLabelStyle}>LAST CLASS</span><span style={metaValueStyle}>{routingClass ? routingClass.toUpperCase() : AWAITING}</span></div>
          <div style={metaRowStyle}><span style={metaLabelStyle}>LATENCY</span><span style={metaValueStyle}>O: {openaiLatency !== null ? `${Math.round(openaiLatency)}ms` : AWAITING} / G: {grokLatency !== null ? `${Math.round(grokLatency)}ms` : AWAITING}</span></div>
          <div style={metaRowStyle}><span style={metaLabelStyle}>MOD BLOCKS</span><span style={metaValueStyle}>O: {countText(openaiBlocks)} / G: {countText(grokBlocks)}</span></div>
          <div style={metaRowStyle}><span style={metaLabelStyle}>ROUTE HITS</span><span style={metaValueStyle}>M: {countText(musicHits)} / G: {countText(generalHits)} / O: {countText(overrideHits)}</span></div>
          <div style={metaRowStyle}><span style={metaLabelStyle}>MEMORY</span><span style={metaValueStyle}>{memoryReachable === true ? "OK" : (memoryReachable === false ? "ERR" : AWAITING)}</span></div>
          <div style={{ ...metaRowStyle, borderBottom: "none" }}><span style={metaLabelStyle}>READINESS</span><span style={{ ...metaValueStyle, color: ready ? "#2ecc40" : "#ff851b" }}>{readinessText}</span></div>
        </div>
      </RackPanel>
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <RackPanel>
          <RackLabel>Usage - Today</RackLabel>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
            <div>
              <div style={{ fontSize: 28, fontWeight: 800, color: "#ccc", fontFamily: "'JetBrains Mono', monospace", lineHeight: 1 }}>{dailyCostText}</div>
              <div style={{ fontSize: 10, color: "#555", letterSpacing: 1.5, fontFamily: "'JetBrains Mono', monospace", marginTop: 4 }}>COST TODAY</div>
            </div>
            <div>
              <div style={{ fontSize: 28, fontWeight: 800, color: "#ccc", fontFamily: "'JetBrains Mono', monospace", lineHeight: 1 }}>{requestsUsed !== null && Number.isFinite(requestsUsed) ? requestsUsed : AWAITING}</div>
              <div style={{ fontSize: 10, color: "#555", letterSpacing: 1.5, fontFamily: "'JetBrains Mono', monospace", marginTop: 4 }}>API CALLS</div>
            </div>
          </div>
          {requestsMax > 0 && requestsUsed !== null && Number.isFinite(requestsUsed) && <MeterBar value={Math.min(requestsUsed, requestsMax)} max={requestsMax} color="#7faacc" label={`Daily request cap (${requestsMax})`} />}
        </RackPanel>
        <RackPanel>
          <RackLabel>Provider Switch - Pre-Approved Only</RackLabel>
          {providers.map((p) => (
            <button key={p.id} onClick={() => setProviderActive(p.id)} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", width: "100%", background: ap === p.id ? "#2a2a2e" : "transparent", border: `1px solid ${ap === p.id ? "#7faacc44" : "#252528"}`, borderRadius: 3, padding: "10px 14px", marginBottom: 6, cursor: "pointer", boxSizing: "border-box" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <Led color={ap === p.id ? "#2ecc40" : "#555"} size={6} />
                <span style={{ fontSize: 12, color: ap === p.id ? "#ccc" : "#666", fontFamily: "'JetBrains Mono', monospace", fontWeight: 600 }}>{p.name}</span>
                <span style={{ fontSize: 9, color: "#555", fontFamily: "'JetBrains Mono', monospace" }}>{p.model !== AWAITING ? p.model : ""}</span>
              </div>
              <span style={{ fontSize: 9, letterSpacing: 1.5, color: ap === p.id ? "#2ecc40" : "#555", fontFamily: "'JetBrains Mono', monospace", fontWeight: 700 }}>{ap === p.id ? "ACTIVE" : "STANDBY"}</span>
            </button>
          ))}
          {!providers.length && <AwaitingBlock style={{ padding: "6px 0" }} message="No providers configured" />}
          <div style={{ display: "flex", flexDirection: "column", gap: 0, background: "#111114", border: "1px solid #1f1f22", borderRadius: 2, padding: "6px 10px", marginTop: 8 }}>
            <div style={metaRowStyle}><span style={metaLabelStyle}>OPENAI MODEL</span><span style={metaValueStyle}>{openaiModel}</span></div>
            <div style={metaRowStyle}><span style={metaLabelStyle}>DIRECTOR MODEL</span><span style={metaValueStyle}>{directorModel}</span></div>
            <div style={{ ...metaRowStyle, borderBottom: "none" }}><span style={metaLabelStyle}>GROK MODEL</span><span style={metaValueStyle}>{grokModel}</span></div>
          </div>
          <div style={{ marginTop: 6, fontSize: 10, color: "#555", fontFamily: "'IBM Plex Sans', sans-serif" }}>
            {routingEnabled ? "Grok receives music/culture routes when Routing is ON" : "Grok routing disabled (Routing OFF)"}
          </div>
          <div style={{ marginTop: 10, display: "flex", gap: 8 }}>
            <RackButton label={`ROUTING ${routingEnabled ? "ON" : "OFF"}`} color={routingEnabled ? "#2ecc40" : "#ff851b"} onClick={() => setRoutingEnabled(!routingEnabled)} />
            <RackButton label={`DIRECTOR ${activeDirector === "OfflineDirector" ? "OFFLINE" : "PROVIDER"}`} color="#7faacc" onClick={() => setActiveDirector(activeDirector === "OfflineDirector" ? "ProviderDirector" : "OfflineDirector")} />
            <RackButton label={`DRY_RUN ${dryRunEnabled ? "ON" : "OFF"}`} color={dryRunEnabled ? "#ff4136" : "#2ecc40"} onClick={() => setDryRunEnabled(!dryRunEnabled)} />
          </div>
        </RackPanel>
      </div>
    </div>
  );
}

// --- PAGE: AUTH & ACCOUNTS ---

function AuthPage({ twitchStatusData, twitchConnectStart, twitchConnectPoll, twitchDisconnect, twitchNotice, setTwitchNotice }) {
  const accounts = twitchStatusData?.accounts || {};
  const bot = accounts.bot || {};
  const broadcaster = accounts.broadcaster || {};
  const [connectingAccounts, setConnectingAccounts] = useState({});
  const [disconnectingAccounts, setDisconnectingAccounts] = useState({});
  const connectInFlightRef = useRef({ bot: false, broadcaster: false });
  const devicePopupRefs = useRef({ bot: null, broadcaster: null });
  const authFlow = String(twitchStatusData?.auth_flow || "authorization_code").trim().toLowerCase() || "authorization_code";
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
  const lastRefresh = twitchStatusData?.last_checked_ts ? fmtTime(twitchStatusData.last_checked_ts) : "Not checked";
  const healthOk = Boolean(bot.connected || broadcaster.connected);
  const healthLabel = healthOk ? "OK" : "Not connected";

  const setAccountConnecting = (accountId, connecting) => {
    const normalized = String(accountId || "").toLowerCase();
    if (!normalized) return;
    setConnectingAccounts((prev) => ({ ...prev, [normalized]: Boolean(connecting) }));
  };

  const setAccountDisconnecting = (accountId, disconnecting) => {
    const normalized = String(accountId || "").toLowerCase();
    if (!normalized) return;
    setDisconnectingAccounts((prev) => ({ ...prev, [normalized]: Boolean(disconnecting) }));
  };

  const closePopupForAccount = (accountId) => {
    const normalized = String(accountId || "").toLowerCase();
    if (!normalized) return;
    const popup = devicePopupRefs.current[normalized];
    try {
      if (popup && !popup.closed) popup.close();
    } catch (_e) {
      // cross-origin access can throw after redirect; ignore
    }
    devicePopupRefs.current[normalized] = null;
  };

  useEffect(() => {
    if (authFlow !== "device_code") return undefined;
    const pendingAccounts = [bot, broadcaster].filter((acct) => Boolean(acct?.pending_auth?.active));
    if (!pendingAccounts.length) return undefined;
    let cancelled = false;
    const poll = async () => {
      for (const acct of pendingAccounts) {
        const accountId = String(acct?.account || "").toLowerCase();
        if (!accountId) continue;
        const result = await twitchConnectPoll(accountId);
        if (cancelled) return;
        if (!result?.ok) {
          const detail = String(result?.body?.detail || result?.body?.error || "Device authorization failed.");
          setTwitchNotice(detail);
          connectInFlightRef.current[accountId] = false;
          setAccountConnecting(accountId, false);
          closePopupForAccount(accountId);
          continue;
        }
        if (result?.body?.connected) {
          setTwitchNotice(`${accountId} connected.`);
          connectInFlightRef.current[accountId] = false;
          setAccountConnecting(accountId, false);
          closePopupForAccount(accountId);
        } else if (result?.body?.pending) {
          const code = String(result?.body?.user_code || acct?.pending_auth?.user_code || "").trim();
          if (code) {
            setTwitchNotice(`Waiting for Twitch approval. Enter code ${code}.`);
          }
        }
      }
    };
    const interval = setInterval(poll, 3000);
    poll();
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [
    authFlow,
    bot?.account,
    bot?.pending_auth?.active,
    bot?.pending_auth?.user_code,
    broadcaster?.account,
    broadcaster?.pending_auth?.active,
    broadcaster?.pending_auth?.user_code,
    twitchConnectPoll,
    setTwitchNotice,
  ]);

  // Clear in-flight guard when account status flips to connected.
  useEffect(() => {
    for (const accountId of ["bot", "broadcaster"]) {
      const acct = accounts?.[accountId];
      if (acct?.connected === true && connectInFlightRef.current[accountId]) {
        connectInFlightRef.current[accountId] = false;
        setAccountConnecting(accountId, false);
        closePopupForAccount(accountId);
      }
    }
  }, [accounts]);

  const AcctRow = ({ account }) => {
    const hasConnectedState = typeof account?.connected === "boolean";
    const connected = hasConnectedState ? account.connected : null;
    const reason = String(account?.reason || "").trim();
    const reasonText = reason ? reason.replaceAll("_", " ") : "\u2014";
    const accountName = String(account?.display_name || "").trim() || "Unknown account";
    const accountRole = String(account?.role || "").trim() || "\u2014";
    const accountId = String(account?.account || "").toLowerCase();
    const canDisconnect = Boolean(account?.disconnect_available && accountId);
    const canConnect = Boolean(account?.connect_available && accountId);
    const pendingAuth = account?.pending_auth && account.pending_auth.active ? account.pending_auth : null;
    const isConnecting = Boolean(connectingAccounts[accountId]);
    const isDisconnecting = Boolean(disconnectingAccounts[accountId]);
    const connectDisabled = !canConnect || Boolean(pendingAuth) || isConnecting || isDisconnecting;
    const disconnectDisabled = !canDisconnect || isDisconnecting || isConnecting;
    const flowLabel = String(account?.auth_flow || authFlow || "authorization_code")
      .toUpperCase()
      .replaceAll("_", " ");
    const statusLabel = connected === true ? "CONNECTED" : (connected === false ? "DISCONNECTED" : "UNKNOWN");
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
          {connected === true ? "Status verified by backend." : (connected === false ? `Reason: ${reasonText || "\u2014"}` : "Status unknown")}
        </div>
        <div style={{ ...TEXT_STYLES.meta, marginBottom: 8 }}>
          FLOW: {flowLabel}
        </div>
        {pendingAuth ? (
          <div style={{ background: "#111114", border: "1px solid #2a2a2e", borderRadius: 2, padding: "8px 10px", marginBottom: 8 }}>
            <div style={{ ...TEXT_STYLES.meta, marginBottom: 4 }}>DEVICE CODE</div>
            <div style={{ fontSize: 14, color: "#7faacc", fontFamily: "'JetBrains Mono', monospace", fontWeight: 700, letterSpacing: 1 }}>
              {String(pendingAuth.user_code || "\u2014")}
            </div>
            <div style={{ ...TEXT_STYLES.meta, marginTop: 4 }}>
              {String(pendingAuth.verification_uri || "").trim() || "Open Twitch activation page to approve."}
            </div>
          </div>
        ) : null}
        <div style={{ display: "flex", gap: 8 }}>
          <RackButton
            label={
              isConnecting
                ? "CONNECTING..."
                : (pendingAuth
                  ? "PENDING APPROVAL"
                  : (canConnect ? (connected === true ? "RECONNECT" : "CONNECT") : "NOT AVAILABLE"))
            }
            color="#7faacc"
            disabled={connectDisabled}
            onClick={async () => {
              if (connectInFlightRef.current[accountId]) return;
              connectInFlightRef.current[accountId] = true;
              setAccountConnecting(accountId, true);
              // Open placeholder popup immediately (synchronous) to avoid popup blockers
              const placeholder = window.open("about:blank", "_blank", "popup,width=560,height=760");
              devicePopupRefs.current[accountId] = placeholder;
              try {
                const result = await twitchConnectStart(accountId);
                const authUrl = result?.body?.auth_url;
                const flow = String(result?.body?.flow || "").toLowerCase();
                if (authUrl) {
                  if (placeholder && !placeholder.closed) {
                    placeholder.location = authUrl;
                  } else {
                    setTwitchNotice(`Popup blocked. Open this URL manually: ${authUrl}`);
                  }
                  setTwitchNotice("Twitch authorization started in a new tab.");
                } else if (result?.ok && flow === "device_code") {
                  const verificationUri = String(result?.body?.verification_uri_complete || result?.body?.verification_uri || "").trim();
                  const userCode = String(result?.body?.user_code || "").trim();
                  if (verificationUri && placeholder && !placeholder.closed) {
                    placeholder.location = verificationUri;
                  } else if (verificationUri) {
                    window.open(verificationUri, "_blank", "popup,width=560,height=760");
                  }
                  if (userCode && verificationUri) {
                    setTwitchNotice(`Device auth started. Enter code ${userCode} at ${verificationUri}.`);
                  } else if (userCode) {
                    setTwitchNotice(`Device auth started. Enter code ${userCode} on Twitch activation page.`);
                  } else {
                    setTwitchNotice("Device auth started. Complete approval in Twitch.");
                  }
                } else if (!result?.ok) {
                  const detail = String(result?.body?.detail || result?.body?.error || "Unknown error");
                  setTwitchNotice(detail);
                  console.error(`[Dashboard Twitch] reconnect unavailable for ${accountId}:`, detail);
                  connectInFlightRef.current[accountId] = false;
                  setAccountConnecting(accountId, false);
                  closePopupForAccount(accountId);
                }
              } catch (err) {
                setTwitchNotice(String(err || "Unable to start Twitch auth."));
                connectInFlightRef.current[accountId] = false;
                setAccountConnecting(accountId, false);
                closePopupForAccount(accountId);
              }
            }}
          />
          <RackButton
            label={pendingAuth ? "CHECK APPROVAL" : "CHECK"}
            color="#ff851b"
            disabled={!pendingAuth}
            onClick={async () => {
              const result = await twitchConnectPoll(accountId);
              if (!result?.ok) {
                const detail = String(result?.body?.detail || result?.body?.error || "Device authorization failed.");
                setTwitchNotice(detail);
                connectInFlightRef.current[accountId] = false;
                setAccountConnecting(accountId, false);
                closePopupForAccount(accountId);
              } else if (result?.body?.connected) {
                setTwitchNotice(`${accountId} connected.`);
                connectInFlightRef.current[accountId] = false;
                setAccountConnecting(accountId, false);
                closePopupForAccount(accountId);
              } else {
                setTwitchNotice("Authorization still pending.");
              }
            }}
          />
          <RackButton
            label={isDisconnecting ? "DISCONNECTING..." : (canDisconnect ? "DISCONNECT" : "NOT AVAILABLE")}
            color="#ff4136"
            disabled={disconnectDisabled}
            onClick={async () => {
              if (isDisconnecting) return;
              setAccountDisconnecting(accountId, true);
              try {
                await twitchDisconnect(accountId);
              } finally {
                connectInFlightRef.current[accountId] = false;
                setAccountConnecting(accountId, false);
                setAccountDisconnecting(accountId, false);
                closePopupForAccount(accountId);
              }
            }}
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
              {c.on !== true && <AwaitingInline style={{ marginLeft: "auto" }} message="Not granted" />}
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
          <div style={{ ...TEXT_STYLES.body, marginBottom: 8 }}>{primaryChannel || "Not configured"}</div>
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
              @{item.viewer_handle || "unknown"}
            </div>
            <div style={{ fontSize: 12, color: "#999", fontFamily: "'IBM Plex Sans', sans-serif", lineHeight: 1.4, marginBottom: 8 }}>
              {item.proposed_note || "No note text"}
            </div>
            <div style={{ display: "flex", gap: 6 }}>
              <button onClick={() => approvePending(item)} style={{ background: "none", border: "1px solid #2ecc4044", borderRadius: 2, color: "#2ecc40", padding: "2px 8px", fontSize: 10, cursor: "pointer", fontFamily: "'JetBrains Mono', monospace" }}>APPROVE</button>
              <button onClick={() => denyPending(item)} style={{ background: "none", border: "1px solid #ff851b44", borderRadius: 2, color: "#ff851b", padding: "2px 8px", fontSize: 10, cursor: "pointer", fontFamily: "'JetBrains Mono', monospace" }}>DENY</button>
            </div>
          </div>
        ))}
        {!pendingNotes.length ? <div style={{ padding: "6px 0", fontSize: 11, color: "#666", fontFamily: "'IBM Plex Sans', sans-serif" }}>No pending proposals</div> : null}

        <div style={{ borderTop: "1px solid #1f1f22", margin: "10px 0" }} />
        <RackLabel>Cultural Notes - Room Level</RackLabel>
        <div style={{ fontSize: 10, color: "#555", fontFamily: "'IBM Plex Sans', sans-serif", marginBottom: 12 }}>These shape how Roonie reads the room and responds. Apply to all interactions.</div>
        {culturalNotes.map((item) => (
          <div key={item.id} onClick={() => editCulturalNote(item)} style={{ padding: "10px 12px", borderLeft: "2px solid #7faacc44", marginBottom: 6, background: "#15151a", borderRadius: "0 2px 2px 0", cursor: "pointer" }}>
            <div style={{ fontSize: 12, color: "#aaa", fontFamily: "'IBM Plex Sans', sans-serif", lineHeight: 1.5 }}>{item.note}</div>
          </div>
        ))}
        {!culturalNotes.length ? <div style={{ padding: "6px 0", fontSize: 11, color: "#666", fontFamily: "'IBM Plex Sans', sans-serif" }}>No cultural notes added</div> : null}
        <button onClick={addCulturalNote} style={{ marginTop: 8, background: "transparent", border: "1px dashed #333", color: "#555", padding: "8px 16px", fontSize: 10, letterSpacing: 1.5, fontFamily: "'JetBrains Mono', monospace", cursor: "pointer", borderRadius: 2, width: "100%" }}>+ ADD CULTURAL NOTE</button>
      </RackPanel>
      <RackPanel><RackLabel>Viewer Notes - Individual</RackLabel><div style={{ fontSize: 10, color: "#555", fontFamily: "'IBM Plex Sans', sans-serif", marginBottom: 12 }}>Observable behavior only. No subjective labels or inferred traits.</div>{viewerNotes.map((v) => (<div key={v.id} style={{ padding: "10px 12px", marginBottom: 6, background: "#15151a", border: "1px solid #222", borderRadius: 2, display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 10 }}><div style={{ flex: 1 }}><div style={{ fontSize: 11, color: "#7faacc", fontFamily: "'JetBrains Mono', monospace", fontWeight: 700, marginBottom: 4 }}>@{v.viewer_handle}</div><div style={{ fontSize: 12, color: "#999", fontFamily: "'IBM Plex Sans', sans-serif", lineHeight: 1.4 }}>{v.note}</div></div><div style={{ display: "flex", gap: 4, flexShrink: 0 }}><button onClick={() => editViewerNote(v)} style={{ background: "none", border: "1px solid #333", borderRadius: 2, color: "#666", padding: "2px 6px", fontSize: 10, cursor: "pointer", fontFamily: "'JetBrains Mono', monospace" }}>EDIT</button><button onClick={() => deleteViewerNote(v.id)} style={{ background: "none", border: "1px solid #ff413633", borderRadius: 2, color: "#ff4136", padding: "2px 6px", fontSize: 10, cursor: "pointer", fontFamily: "'JetBrains Mono', monospace" }}>X</button></div></div>))}{!viewerNotes.length ? <div style={{ padding: "6px 0", fontSize: 11, color: "#666", fontFamily: "'IBM Plex Sans', sans-serif" }}>No viewer notes added</div> : null}<button onClick={addViewerNote} style={{ marginTop: 8, background: "transparent", border: "1px dashed #333", color: "#555", padding: "8px 16px", fontSize: 10, letterSpacing: 1.5, fontFamily: "'JetBrains Mono', monospace", cursor: "pointer", borderRadius: 2, width: "100%" }}>+ ADD VIEWER NOTE</button></RackPanel>
    </div>
  );
}

// --- PAGE: INNER CIRCLE ---

function InnerCirclePage({ innerCircleData, saveInnerCircle }) {
  const members = Array.isArray(innerCircleData?.members) ? innerCircleData.members : [];
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState([]);

  const inputStyle = { background: "#111114", border: "1px solid #2a2a2e", borderRadius: 2, padding: "4px 8px", color: "#aaa", fontSize: 11, fontFamily: "'IBM Plex Sans', sans-serif", outline: "none", boxSizing: "border-box" };
  const editBtnStyle = { background: "transparent", border: "1px dashed #333", color: "#555", padding: "8px 16px", fontSize: 10, letterSpacing: 1.5, fontFamily: "'JetBrains Mono', monospace", cursor: "pointer", borderRadius: 2, width: "100%" };
  const saveBtnStyle = { ...editBtnStyle, border: "1px solid #2ecc4066", color: "#2ecc40" };
  const cancelBtnStyle = { ...editBtnStyle };

  const startEditing = () => {
    setDraft(members.map((m) => ({ ...m })));
    setEditing(true);
  };

  const cancelEditing = () => setEditing(false);

  const cleanUsername = (raw) => String(raw || "").trim().replace(/^@+/, "").toLowerCase();

  const saveMembers = async () => {
    const cleaned = draft
      .filter((m) => cleanUsername(m.username))
      .map((m) => ({
        username: cleanUsername(m.username),
        display_name: String(m.display_name || "").trim(),
        role: String(m.role || "").trim(),
        note: String(m.note || "").trim(),
      }));
    const seen = new Set();
    const deduped = cleaned.filter((m) => {
      if (seen.has(m.username)) return false;
      seen.add(m.username);
      return true;
    });
    await saveInnerCircle({ version: 1, members: deduped });
    setEditing(false);
  };

  const updateDraftField = (idx, field, value) => {
    const next = [...draft];
    next[idx] = { ...next[idx], [field]: value };
    setDraft(next);
  };

  return (
    <div>
      <RackPanel>
        <RackLabel>Inner Circle</RackLabel>
        <div style={{ fontSize: 10, color: "#555", fontFamily: "'IBM Plex Sans', sans-serif", marginBottom: 12 }}>
          People Roonie knows personally. This list is injected into his prompt so he recognizes them in chat.
        </div>
        {editing ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {draft.length > 0 && (
              <div style={{ display: "grid", gridTemplateColumns: "120px 120px 90px 1fr 20px", gap: 6, marginBottom: 2 }}>
                <span style={{ fontSize: 9, color: "#5a5a5a", letterSpacing: 1.5, fontFamily: "'JetBrains Mono', monospace", fontWeight: 600 }}>USERNAME</span>
                <span style={{ fontSize: 9, color: "#5a5a5a", letterSpacing: 1.5, fontFamily: "'JetBrains Mono', monospace", fontWeight: 600 }}>DISPLAY NAME</span>
                <span style={{ fontSize: 9, color: "#5a5a5a", letterSpacing: 1.5, fontFamily: "'JetBrains Mono', monospace", fontWeight: 600 }}>ROLE</span>
                <span style={{ fontSize: 9, color: "#5a5a5a", letterSpacing: 1.5, fontFamily: "'JetBrains Mono', monospace", fontWeight: 600 }}>NOTE</span>
                <span />
              </div>
            )}
            {draft.map((m, i) => (
              <div key={i} style={{ display: "grid", gridTemplateColumns: "120px 120px 90px 1fr 20px", gap: 6, alignItems: "center" }}>
                <input type="text" value={m.username} onChange={(e) => updateDraftField(i, "username", e.target.value)} placeholder="username" style={inputStyle} />
                <input type="text" value={m.display_name || ""} onChange={(e) => updateDraftField(i, "display_name", e.target.value)} placeholder="display name" style={inputStyle} />
                <input type="text" value={m.role || ""} onChange={(e) => updateDraftField(i, "role", e.target.value)} placeholder="role" style={inputStyle} />
                <input type="text" value={m.note || ""} onChange={(e) => updateDraftField(i, "note", e.target.value)} placeholder="context about this person..." style={inputStyle} />
                <span onClick={() => { const next = [...draft]; next.splice(i, 1); setDraft(next); }} style={{ color: "#ff4136", fontSize: 13, cursor: "pointer", fontFamily: "'JetBrains Mono', monospace", textAlign: "center", flexShrink: 0 }}>&times;</span>
              </div>
            ))}
            <div onClick={() => setDraft([...draft, { username: "", display_name: "", role: "", note: "" }])} style={{ fontSize: 10, color: "#555", letterSpacing: 1, fontFamily: "'JetBrains Mono', monospace", cursor: "pointer", marginTop: 4 }}>+ ADD MEMBER</div>
            <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
              <button onClick={saveMembers} style={saveBtnStyle}>SAVE</button>
              <button onClick={cancelEditing} style={cancelBtnStyle}>CANCEL</button>
            </div>
          </div>
        ) : (
          <div>
            {members.map((m, idx) => (
              <div key={m.username || idx} style={{ padding: "10px 12px", marginBottom: 6, background: "#15151a", border: "1px solid #222", borderRadius: 2 }}>
                <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: m.note ? 4 : 0 }}>
                  <span style={{ fontSize: 11, color: "#7faacc", fontFamily: "'JetBrains Mono', monospace", fontWeight: 700 }}>@{m.username}</span>
                  {m.display_name ? <span style={{ fontSize: 11, color: "#999", fontFamily: "'IBM Plex Sans', sans-serif" }}>{m.display_name}</span> : null}
                  {m.role ? <span style={{ fontSize: 9, color: "#555", fontFamily: "'JetBrains Mono', monospace", letterSpacing: 1, textTransform: "uppercase", background: "#1a1a1e", padding: "1px 5px", borderRadius: 2 }}>{m.role}</span> : null}
                </div>
                {m.note ? <div style={{ fontSize: 12, color: "#888", fontFamily: "'IBM Plex Sans', sans-serif", lineHeight: 1.4 }}>{m.note}</div> : null}
              </div>
            ))}
            {!members.length ? <div style={{ padding: "6px 0", fontSize: 11, color: "#666", fontFamily: "'IBM Plex Sans', sans-serif" }}>No members added</div> : null}
            <button onClick={startEditing} style={{ ...editBtnStyle, marginTop: 8 }}>EDIT INNER CIRCLE</button>
          </div>
        )}
      </RackPanel>
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
    : "No context carry";

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
                <span style={{ fontSize: 11, color: "#aaa", fontFamily: "'JetBrains Mono', monospace" }}>{events.length ? events.length : <AwaitingInline message="0" />}</span>
              </div>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <span style={{ fontSize: 11, color: "#888", fontFamily: "'IBM Plex Sans', sans-serif" }}>Direct Address Count</span>
                <span style={{ fontSize: 11, color: "#aaa", fontFamily: "'JetBrains Mono', monospace" }}>{events.length ? directAddressCount : <AwaitingInline message="0" />}</span>
              </div>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <span style={{ fontSize: 11, color: "#888", fontFamily: "'IBM Plex Sans', sans-serif" }}>Spoken Decisions</span>
                <span style={{ fontSize: 11, color: "#aaa", fontFamily: "'JetBrains Mono', monospace" }}>{events.length ? speakCount : <AwaitingInline message="0" />}</span>
              </div>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <span style={{ fontSize: 11, color: "#888", fontFamily: "'IBM Plex Sans', sans-serif" }}>Suppression Count</span>
                <span style={{ fontSize: 11, color: "#aaa", fontFamily: "'JetBrains Mono', monospace" }}>{suppressions.length ? suppressionCount : <AwaitingInline message="0" />}</span>
              </div>
            </div>

            <RackLabel>Recent Events</RackLabel>
            {events.map((event, idx) => (
              <div key={idx} style={{ padding: "6px 10px", borderLeft: "2px solid #7faacc33", marginBottom: 4, background: "#15151a", borderRadius: "0 2px 2px 0" }}>
                <div style={{ fontSize: 10, color: "#7faacc", fontFamily: "'JetBrains Mono', monospace", fontWeight: 600, marginBottom: 2 }}>{fmtTime(event.ts)}</div>
                <div style={{ fontSize: 11, color: "#999", fontFamily: "'IBM Plex Sans', sans-serif", lineHeight: 1.5 }}>{buildMessageLine(event) || "\u2014"}</div>
              </div>
            ))}
            {!events.length ? <div style={{ fontSize: 11, color: "#666", fontFamily: "'IBM Plex Sans', sans-serif" }}>No events in sample</div> : null}
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
                <div style={{ fontSize: 11, color: "#aaa", fontFamily: "'IBM Plex Sans', sans-serif" }}>{suppressions[0]?.suppression_reason || "None"}</div>
              </div>
              <div style={{ padding: "5px 0" }}>
                <div style={{ fontSize: 9, color: "#5a5a5a", letterSpacing: 1.5, fontFamily: "'JetBrains Mono', monospace", marginBottom: 2 }}>Last Suppression Detail</div>
                <div style={{ fontSize: 11, color: "#aaa", fontFamily: "'IBM Plex Sans', sans-serif" }}>{suppressions[0]?.suppression_detail || "None"}</div>
              </div>
            </div>

            <RackLabel>Suppression Reasons</RackLabel>
            {suppressions.map((entry, idx) => (
              <div key={idx} style={{ display: "flex", justifyContent: "space-between", padding: "3px 0" }}>
                <span style={{ fontSize: 10, color: "#666", fontFamily: "'JetBrains Mono', monospace" }}>{fmtTime(entry.ts)}</span>
                <span style={{ fontSize: 10, color: "#aaa", fontFamily: "'JetBrains Mono', monospace" }}>{entry.suppression_reason || "\u2014"}</span>
              </div>
            ))}
            {!suppressions.length ? <div style={{ fontSize: 11, color: "#666", fontFamily: "'IBM Plex Sans', sans-serif" }}>No suppressions in sample</div> : null}
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
  const guardrailsText = `Local-only: ${typeof status.local_only === "boolean" ? (status.local_only ? "yes" : "no") : "\u2014"} | Whitelist: ${whitelist ? whitelist.join(", ") : "none"} | Never initiate: ${typeof status.never_initiate === "boolean" ? (status.never_initiate ? "yes" : "no") : "\u2014"} | No viewer recognition: ${typeof status.no_viewer_recognition === "boolean" ? (status.no_viewer_recognition ? "yes" : "no") : "\u2014"}`;
  const statusReason = status.reason || "No reason provided";
  const purpose = status.purpose || "text-only operation";
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
  const rules = [
    "Roonie never initiates conversation unprompted.",
    "Roonie limits exclamation points to two maximum. Warmth comes from specificity, not volume.",
    "Roonie goes quieter when chat gets louder - never louder.",
    "Roonie does not reference operator names unless asked.",
    "Roonie does not guess track IDs - uncertainty is stated.",
    "Roonie does not store or surface personal viewer data beyond operator notes.",
    "Silence and restraint are features, not failures.",
    "Roonie uses no Unicode emojis \u2014 only approved Twitch channel emotes, one per message max.",
  ];
  const antiDrift = [
    { label: "Persona editing", status: "LOCKED" },
    { label: "Auto-message scheduling", status: "NOT SUPPORTED" },
    { label: "Viewer profiling", status: "NOT SUPPORTED" },
    { label: "Deep configuration knobs", status: "NOT EXPOSED" },
  ];
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
      <RackPanel>
        <RackLabel>Enforced Behavioral Constraints</RackLabel>
        <div style={{ fontSize: 11, color: "#666", fontFamily: "'IBM Plex Sans', sans-serif", marginBottom: 14, lineHeight: 1.6 }}>
          This page documents Roonie's enforced behavioral constraints. These rules are baked into the persona system and cannot be modified from the dashboard. They exist to prevent drift and ensure consistent behavior.
        </div>
        {rules.map((rule, i) => (
          <div key={i} style={{ padding: "8px 12px", marginBottom: 4, background: "#15151a", borderLeft: "2px solid #2ecc4044", borderRadius: "0 2px 2px 0" }}>
            <div style={{ fontSize: 12, color: "#aaa", fontFamily: "'IBM Plex Sans', sans-serif", lineHeight: 1.5 }}>{rule}</div>
          </div>
        ))}
      </RackPanel>
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <RackPanel>
          <RackLabel>Persona Source</RackLabel>
          <div style={{ padding: "10px 12px", background: "#15151a", border: "1px solid #222", borderRadius: 2, marginBottom: 8 }}>
            <div style={{ fontSize: 12, color: "#aaa", fontFamily: "'IBM Plex Sans', sans-serif", lineHeight: 1.5 }}>Persona is defined in versioned policy files loaded at startup.</div>
          </div>
          <div style={{ padding: "10px 12px", background: "#15151a", border: "1px solid #222", borderRadius: 2 }}>
            <div style={{ fontSize: 12, color: "#aaa", fontFamily: "'IBM Plex Sans', sans-serif", lineHeight: 1.5 }}>Dashboard only edits bounded Style Profile values.</div>
          </div>
          <div style={{ fontSize: 9, color: "#444", fontFamily: "'JetBrains Mono', monospace", marginTop: 10, letterSpacing: 1 }}>NO FREE-TEXT PERSONA EDITOR EXISTS BY DESIGN</div>
        </RackPanel>
        <RackPanel>
          <RackLabel>Anti-Drift</RackLabel>
          <div style={{ fontSize: 11, color: "#666", fontFamily: "'IBM Plex Sans', sans-serif", marginBottom: 12, lineHeight: 1.6 }}>
            These guardrails prevent scope creep. Features not listed here are intentionally out of scope. If you find yourself wanting to add something, check here first.
          </div>
          {antiDrift.map((item, i) => (
            <div key={i} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "7px 0", borderBottom: i < antiDrift.length - 1 ? "1px solid #1f1f22" : "none" }}>
              <span style={{ fontSize: 12, color: "#888", fontFamily: "'IBM Plex Sans', sans-serif" }}>{item.label}</span>
              <span style={{ fontSize: 9, padding: "2px 8px", background: "#ff851b12", border: "1px solid #ff851b33", borderRadius: 2, color: "#ff851b", letterSpacing: 1.5, fontFamily: "'JetBrains Mono', monospace", fontWeight: 700 }}>{item.status}</span>
            </div>
          ))}
        </RackPanel>
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
    innerCircleData,
    twitchStatusData,
    twitchNotice,
    setTwitchNotice,
    performAction,
    busyAction,
    saveStudioProfile,
    saveInnerCircle,
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
    twitchConnectPoll,
    twitchDisconnect,
    fetchChannelEmotes,
  } = useDashboardData(activePage);
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
      case "live": return <LivePage statusData={statusData} eventsData={eventsData} suppressionsData={suppressionsData} performAction={performAction} busyAction={busyAction} />;
      case "studio": return <StudioProfilePage studioProfileData={studioProfileData} saveStudioProfile={saveStudioProfile} fetchChannelEmotes={fetchChannelEmotes} />;
      case "library": return <LibraryIndexPage libraryStatusData={libraryStatusData} librarySearchData={librarySearchData} searchLibraryIndex={searchLibraryIndex} uploadLibraryXml={uploadLibraryXml} />;
      case "announcements": return <AnnouncementsPage queueData={queueData} performAction={performAction} />;
      case "nowplaying": return <NowPlayingPage />;
      case "logs": return <LogsPage eventsData={logsEventsData} suppressionsData={logsSuppressionsData} operatorLogData={logsOperatorData} />;
      case "providers": return <ProvidersPage statusData={statusData} providersStatusData={providersStatusData} routingStatusData={routingStatusData} systemHealthData={systemHealthData} readinessData={readinessData} setProviderActive={setProviderActive} setProviderCaps={setProviderCaps} setRoutingEnabled={setRoutingEnabled} setActiveDirector={setActiveDirector} setDryRunEnabled={setDryRunEnabled} />;
      case "auth": return <AuthPage twitchStatusData={twitchStatusData} twitchConnectStart={twitchConnectStart} twitchConnectPoll={twitchConnectPoll} twitchDisconnect={twitchDisconnect} twitchNotice={twitchNotice} setTwitchNotice={setTwitchNotice} />;
      case "culture": return <CulturePage culturalNotesData={culturalNotesData} viewerNotesData={viewerNotesData} memoryPendingData={memoryPendingData} saveCulturalNote={saveCulturalNote} deleteCulturalNote={deleteCulturalNote} saveViewerNote={saveViewerNote} deleteViewerNote={deleteViewerNote} reviewMemoryPending={reviewMemoryPending} />;
      case "innercircle": return <InnerCirclePage innerCircleData={innerCircleData} saveInnerCircle={saveInnerCircle} />;
      case "snapshot": return <CulturalSnapshotPage logsEventsData={logsEventsData} logsSuppressionsData={logsSuppressionsData} statusData={statusData} />;
      case "senses": return <SensesPage sensesStatusData={sensesStatusData} />;
      case "governance": return <GovernancePage />;
      default: return <LivePage statusData={statusData} eventsData={eventsData} suppressionsData={suppressionsData} performAction={performAction} busyAction={busyAction} />;
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
            <span style={{ fontSize: 11, color: "#ccc", fontFamily: "'IBM Plex Sans', sans-serif", fontWeight: 600 }}>No track loaded</span>
          </div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 8, whiteSpace: "nowrap" }}>
            <span style={{ fontSize: 8, color: "#444", letterSpacing: 2, fontFamily: "'JetBrains Mono', monospace", flexShrink: 0 }}>PREVIOUS</span>
            <span style={{ fontSize: 10, color: "#666", fontFamily: "'IBM Plex Sans', sans-serif" }}>{"\u2014"}</span>
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
              <div>PROVIDER: {(statusData.active_provider ? String(statusData.active_provider).toUpperCase() : "NONE")}</div>
              <div>DIRECTOR: {String(statusData.active_director || "ProviderDirector").toUpperCase()}</div>
              <div>ROUTING: {statusData.routing_enabled ? "ON" : "OFF"}</div>
              <div
                style={{
                  maxWidth: "100%",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
                title={`BUILD: ${statusData.version || "unknown"}`}
              >
                BUILD: {statusData.version || "unknown"}
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
