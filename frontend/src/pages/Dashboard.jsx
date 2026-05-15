import { useEffect, useState } from "react";
import { useAuth } from "../contexts/AuthContext";
import { apiFetch } from "../api";

function formatDate(ts) {
  if (!ts) return "";
  const d = ts?._seconds ? new Date(ts._seconds * 1000) : new Date(ts);
  if (isNaN(d)) return "";
  const now = new Date();
  const diff = now - d;
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (diff < 86_400_000) return `Today · ${time}`;
  if (diff < 172_800_000) return `Yesterday · ${time}`;
  return d.toLocaleDateString([], { month: "short", day: "numeric" }) + ` · ${time}`;
}

function dirLabel(dir) {
  return dir === "de_to_en" ? "DE→EN" : "EN→DE";
}

export default function Dashboard({ onNewSession, onOpenSession }) {
  const { user, getFreshToken } = useAuth();
  const [sessions, setSessions] = useState([]);
  const [selected, setSelected] = useState(null);
  const [messages, setMessages] = useState([]);
  const [loadingSessions, setLoadingSessions] = useState(true);
  const [loadingMessages, setLoadingMessages] = useState(false);

  const firstName = user?.displayName?.split(" ")[0] ?? user?.email?.split("@")[0] ?? "there";
  const hour = new Date().getHours();
  const greeting = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";

  useEffect(() => {
    async function load() {
      try {
        const token = await getFreshToken();
        const data = await apiFetch("/sessions", token);
        setSessions(data);
        if (data.length > 0) setSelected(data[0]);
      } catch (err) {
        console.error("Failed to load sessions:", err);
      } finally {
        setLoadingSessions(false);
      }
    }
    load();
  }, [getFreshToken]);

  useEffect(() => {
    if (!selected) { setMessages([]); return; }
    setLoadingMessages(true);
    async function load() {
      try {
        const token = await getFreshToken();
        const data = await apiFetch(`/sessions/${selected.id}/messages`, token);
        setMessages(data);
      } catch (err) {
        console.error("Failed to load messages:", err);
        setMessages([]);
      } finally {
        setLoadingMessages(false);
      }
    }
    load();
  }, [selected, getFreshToken]);

  async function deleteSession(id) {
    try {
      const token = await getFreshToken();
      await apiFetch(`/sessions/${id}`, token, { method: "DELETE" });
      setSessions((prev) => {
        const next = prev.filter((s) => s.id !== id);
        if (selected?.id === id) setSelected(next[0] ?? null);
        return next;
      });
    } catch (err) {
      console.error("Delete failed:", err);
    }
  }

  const deCount = sessions.filter((s) => s.direction === "de_to_en").length;
  const enCount = sessions.filter((s) => s.direction === "en_to_de").length;

  return (
    <div className="page-dashboard">
      <div className="dash-header">
        <div>
          <h1>{greeting}, {firstName}</h1>
          <p>{sessions.length} sessions</p>
        </div>
        <button className="btn-main" onClick={onNewSession}>
          <i className="ti ti-plus" /> New session
        </button>
      </div>

      <div className="stats-row">
        <div className="stat-card">
          <div className="stat-label">Total sessions</div>
          <div className="stat-val">{sessions.length}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Messages</div>
          <div className="stat-val">{messages.length > 0 && selected ? messages.length : "—"}</div>
          <div className="stat-sub">{selected ? "in selected session" : "select a session"}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">DE→EN</div>
          <div className="stat-val">{deCount}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">EN→DE</div>
          <div className="stat-val">{enCount}</div>
        </div>
      </div>

      <div className="dash-body">
        <div className="session-col">
          <div className="col-head">Recent sessions</div>
          {loadingSessions && (
            <div style={{ padding: "16px 24px", color: "var(--text-faint)", fontSize: 13 }}>Loading…</div>
          )}
          {!loadingSessions && sessions.length === 0 && (
            <div style={{ padding: "16px 24px", color: "var(--text-faint)", fontSize: 13 }}>
              No sessions yet. Start a new one!
            </div>
          )}
          {sessions.map((s) => (
            <div
              key={s.id}
              className={`srow${selected?.id === s.id ? " active" : ""}`}
              onClick={() => setSelected(s)}
            >
              <div className={`srow-icon ${s.direction === "de_to_en" ? "de" : "en"}`}>
                <i className="ti ti-message" />
              </div>
              <div className="srow-info">
                <div className="srow-title">{s.title}</div>
                <div className="srow-meta">{formatDate(s.last_active)}</div>
              </div>
              <span className={`dir-pill ${s.direction === "de_to_en" ? "de" : "en"}`}>
                {dirLabel(s.direction)}
              </span>
            </div>
          ))}
        </div>

        <div className="preview-col">
          {selected ? (
            <>
              <div className="preview-head">
                <div>
                  <div className="preview-title">{selected.title}</div>
                  <div className="preview-date">{formatDate(selected.last_active)} · {dirLabel(selected.direction)}</div>
                </div>
                <div className="preview-actions">
                  <button className="icon-btn" title="Open" onClick={() => onOpenSession(selected.id)}>
                    <i className="ti ti-player-play" />
                  </button>
                  <button className="icon-btn" title="Delete" onClick={() => deleteSession(selected.id)}>
                    <i className="ti ti-trash" />
                  </button>
                </div>
              </div>
              {selected.notes && (
                <div className="notes-box">
                  <div className="notes-label">Notes</div>
                  {selected.notes}
                </div>
              )}
              <div className="preview-msgs">
                {loadingMessages && (
                  <div style={{ color: "var(--text-faint)", fontSize: 13, padding: "12px 0" }}>Loading messages…</div>
                )}
                {!loadingMessages && messages.length === 0 && (
                  <div style={{ color: "var(--text-faint)", fontSize: 13, padding: "12px 0" }}>
                    No messages in this session.
                  </div>
                )}
                {messages.map((m, i) => (
                  <div key={m.id ?? i}>
                    <div className="pmsg src">
                      <div className="pmsg-lang">
                        {selected.direction === "de_to_en" ? "German" : "English"}
                      </div>
                      {m.source_text}
                    </div>
                    <div className="pmsg tr" style={{ marginTop: 6 }}>
                      <div className="pmsg-lang">
                        {selected.direction === "de_to_en" ? "English" : "German"}
                      </div>
                      {m.translated_text}
                    </div>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <div className="empty-preview">
              <i className="ti ti-message-2" />
              {loadingSessions ? "Loading…" : "Select a session to preview"}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
