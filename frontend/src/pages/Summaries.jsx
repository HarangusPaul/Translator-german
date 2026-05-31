import { useEffect, useMemo, useState } from "react";
import { useAuth } from "../contexts/AuthContext";
import { apiFetch } from "../api";

function formatDate(ts) {
  if (!ts) return "";
  const d = ts?._seconds ? new Date(ts._seconds * 1000) : new Date(ts);
  if (isNaN(d)) return "";
  return d.toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" }) +
    " · " +
    d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function directionLabel(direction) {
  return direction === "de_to_en" ? "DE→EN" : "EN→DE";
}

export default function Summaries() {
  const { getFreshToken } = useAuth();
  const [sessions, setSessions] = useState([]);
  const [loadingSessions, setLoadingSessions] = useState(true);
  const [filteringNonEmpty, setFilteringNonEmpty] = useState(false);
  const [search, setSearch] = useState("");

  const [selectedSession, setSelectedSession] = useState(null);
  const [messages, setMessages] = useState([]);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [loadingSummary, setLoadingSummary] = useState(false);

  useEffect(() => {
    async function loadSessions() {
      try {
        const token = await getFreshToken();
        const data = await apiFetch("/sessions", token);

        // Keep only sessions that have at least 1 saved message.
        setFilteringNonEmpty(true);
        const checks = await Promise.all(
          data.map(async (s) => {
            try {
              const msgs = await apiFetch(`/sessions/${s.id}/messages?limit=1`, token);
              return msgs?.length > 0;
            } catch (err) {
              console.error("Failed to check messages for session:", s.id, err);
              return false;
            }
          })
        );
        const nonEmpty = data.filter((_, idx) => checks[idx]);
        setSessions(nonEmpty);
      } catch (err) {
        console.error("Failed to load sessions:", err);
      } finally {
        setFilteringNonEmpty(false);
        setLoadingSessions(false);
      }
    }
    loadSessions();
  }, [getFreshToken]);

  useEffect(() => {
    if (!selectedSession) return undefined;

    let cancelled = false;
    setLoadingMessages(true);

    async function loadMessages() {
      try {
        const token = await getFreshToken();
        const data = await apiFetch(`/sessions/${selectedSession.id}/messages`, token);
        if (!cancelled) setMessages(data);
      } catch (err) {
        console.error("Failed to load transcript:", err);
        if (!cancelled) setMessages([]);
      } finally {
        if (!cancelled) setLoadingMessages(false);
      }
    }

    loadMessages();
    return () => {
      cancelled = true;
    };
  }, [selectedSession, getFreshToken]);

  useEffect(() => {
    if (!selectedSession) return undefined;

    let cancelled = false;
    let timer = null;

    async function refreshSession() {
      setLoadingSummary(true);
      try {
        const token = await getFreshToken();
        const fresh = await apiFetch(`/sessions/${selectedSession.id}`, token);
        if (cancelled) return;
        setSelectedSession((prev) => (prev ? { ...prev, ...fresh } : fresh));

        // If the backend is still generating, poll until it's done.
        if (fresh?.summary_status === "pending") {
          timer = window.setTimeout(refreshSession, 2500);
        }
      } catch (err) {
        console.error("Failed to refresh session summary:", err);
      } finally {
        if (!cancelled) setLoadingSummary(false);
      }
    }

    refreshSession();

    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [selectedSession?.id, getFreshToken]);

  useEffect(() => {
    if (!selectedSession) return undefined;

    function onKeyDown(e) {
      if (e.key === "Escape") {
        setSelectedSession(null);
        setMessages([]);
      }
    }

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [selectedSession]);

  const filteredSessions = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return sessions;
    return sessions.filter((s) => {
      const haystack = `${s.title ?? ""} ${s.notes ?? ""}`.toLowerCase();
      return haystack.includes(q);
    });
  }, [sessions, search]);

  function openModal(session) {
    setSelectedSession(session);
    setMessages([]);
  }

  function closeModal() {
    setSelectedSession(null);
    setMessages([]);
  }

  return (
    <div className="page-summaries">
      <div className="sum-header">
        <div>
          <h1>Summaries</h1>
          <p>{sessions.length} sessions{filteringNonEmpty ? " (filtering…)" : ""}</p>
        </div>
      </div>

      <div className="sum-toolbar">
        <div className="search-box">
          <i className="ti ti-search" style={{ fontSize: 13 }} />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search sessions…"
          />
        </div>
      </div>

      <div className="sum-table-head">
        <div className="th">Session</div>
        <div className="th">Direction</div>
        <div className="th">Last active</div>
      </div>

      {loadingSessions && (
        <div className="sum-empty">Loading sessions…</div>
      )}

      {!loadingSessions && filteredSessions.map((s) => (
        <button key={s.id} className="sum-row" onClick={() => openModal(s)}>
          <div>
            <div className="hr-title">{s.title}</div>
            {s.notes && <div className="hr-preview">{s.notes}</div>}
          </div>
          <div>
            <span className={`hr-dir ${s.direction === "de_to_en" ? "de" : "en"}`}>
              {directionLabel(s.direction)}
            </span>
          </div>
          <div className="hr-date">{formatDate(s.last_active)}</div>
        </button>
      ))}

      {!loadingSessions && filteredSessions.length === 0 && (
        <div className="sum-empty">
          {sessions.length === 0 ? "No sessions yet." : "No sessions match your search."}
        </div>
      )}

      {selectedSession && (
        <div className="sum-modal-backdrop" onClick={closeModal}>
          <div className="sum-modal" onClick={(e) => e.stopPropagation()}>
            <div className="sum-modal-head">
              <div>
                <div className="sum-modal-title">{selectedSession.title}</div>
                <div className="preview-date">
                  {formatDate(selectedSession.last_active)} · {directionLabel(selectedSession.direction)}
                </div>
              </div>
              <button className="icon-btn" title="Close" onClick={closeModal}>
                <i className="ti ti-x" />
              </button>
            </div>

            {loadingSummary && !selectedSession.summary && selectedSession.summary_status === "pending" && (
              <div className="sum-modal-summary">
                <div className="notes-label">Summary</div>
                Generating summary…
              </div>
            )}

            {selectedSession.summary && (
              <div className="sum-modal-summary">
                <div className="notes-label">Summary</div>
                {selectedSession.summary}
              </div>
            )}

            {!selectedSession.summary && selectedSession.summary_status === "error" && (
              <div className="sum-modal-summary">
                <div className="notes-label">Summary</div>
                Summary generation failed.
              </div>
            )}

            <div className="sum-modal-body">
              {loadingMessages && <div className="sum-empty">Loading transcript…</div>}
              {!loadingMessages && messages.length === 0 && (
                <div className="sum-empty">No transcript messages for this session.</div>
              )}
              {!loadingMessages && messages.map((m, i) => (
                <div key={m.id ?? i} className="sum-msg-pair">
                  <div className="pmsg src">
                    <div className="pmsg-lang">
                      {selectedSession.direction === "de_to_en" ? "German" : "English"}
                    </div>
                    {m.source_text}
                  </div>
                  <div className="pmsg tr" style={{ marginTop: 6 }}>
                    <div className="pmsg-lang">
                      {selectedSession.direction === "de_to_en" ? "English" : "German"}
                    </div>
                    {m.translated_text}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
