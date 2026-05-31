import { useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "../contexts/AuthContext";
import { apiFetch } from "../api";


function formatDate(ts) {
  if (!ts) return "";
  const d = ts?._seconds ? new Date(ts._seconds * 1000) : new Date(ts);
  if (isNaN(d)) return "";
  const now  = new Date();
  const diff = now - d;
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (diff < 86_400_000)  return `Today, ${time}`;
  if (diff < 172_800_000) return `Yesterday, ${time}`;
  return d.toLocaleDateString([], { month: "short", day: "numeric" }) + `, ${time}`;
}

function dirLabel(dir) {
  return dir === "de_to_en" ? "DE→EN" : "EN→DE";
}

function SummaryBadge({ status, summary }) {
  if (summary && status === "done")
    return <span className="sum-badge done" title="Has summary"><i className="ti ti-check" /></span>;
  if (status === "pending")
    return <span className="sum-badge pending" title="Generating…"><i className="ti ti-loader-2 spin" /></span>;
  if (status === "error")
    return <span className="sum-badge error" title="Generation failed"><i className="ti ti-alert-triangle" /></span>;
  return <span className="sum-badge none" title="No summary yet"><i className="ti ti-minus" /></span>;
}

export default function Summaries() {
  const { getFreshToken } = useAuth();

  const [sessions,         setSessions]         = useState([]);
  const [loadingSessions,  setLoadingSessions]  = useState(true);
  const [search,           setSearch]           = useState("");

  const [selectedSession,  setSelectedSession]  = useState(null);
  const [messages,         setMessages]         = useState([]);
  const [loadingMessages,  setLoadingMessages]  = useState(false);
  const [generating,       setGenerating]       = useState(false);
  const [analysis,         setAnalysis]         = useState(null);

  // ── Load session list ────────────────────────────────────────────────────

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const token = await getFreshToken();
        const all   = await apiFetch("/sessions", token);
        // Filter out sessions with no messages (check each in parallel, limit 1)
        const checks = await Promise.all(
          all.map(async (s) => {
            try {
              const msgs = await apiFetch(`/sessions/${s.id}/messages?limit=1`, token);
              return msgs?.length > 0;
            } catch {
              return false;
            }
          })
        );
        if (!cancelled) setSessions(all.filter((_, i) => checks[i]));
      } catch (err) {
        console.error("Failed to load sessions:", err);
      } finally {
        if (!cancelled) setLoadingSessions(false);
      }
    }
    load();
    return () => { cancelled = true; };
  }, [getFreshToken]);

  // ── Load messages when a session is opened ───────────────────────────────

  useEffect(() => {
    if (!selectedSession) return;
    let cancelled = false;
    setLoadingMessages(true);
    async function load() {
      try {
        const token = await getFreshToken();
        const data  = await apiFetch(`/sessions/${selectedSession.id}/messages`, token);
        if (!cancelled) setMessages(data ?? []);
      } catch {
        if (!cancelled) setMessages([]);
      } finally {
        if (!cancelled) setLoadingMessages(false);
      }
    }
    load();
    return () => { cancelled = true; };
  }, [selectedSession?.id, getFreshToken]);

  // (polling removed — /summarize returns the result synchronously)

  // ── Keyboard: Escape closes modal ────────────────────────────────────────

  useEffect(() => {
    if (!selectedSession) return;
    const onKey = (e) => { if (e.key === "Escape") closeModal(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selectedSession]);

  // ── Actions ──────────────────────────────────────────────────────────────

  function openModal(session) {
    setSelectedSession(session);
    setMessages([]);
    setAnalysis(null);
  }

  function closeModal() {
    setSelectedSession(null);
    setMessages([]);
    setAnalysis(null);
  }

  async function triggerSummary() {
    if (!selectedSession || generating || loadingMessages) return;
    setGenerating(true);
    setSelectedSession((prev) => prev ? { ...prev, summary: null, summary_status: "pending" } : prev);
    try {
      const token = await getFreshToken();

      const ts = selectedSession.created_at;
      const conversation_time = ts?._seconds
        ? new Date(ts._seconds * 1000).toISOString()
        : new Date().toISOString();

      const output_language = selectedSession.direction === "de_to_en" ? "English" : "German";

      const msgPayload = messages.map((m) => ({
        sender:          m.direction === "de_to_en" ? "german_speaker" : "english_speaker",
        source_text:     m.source_text     || "",
        translated_text: m.translated_text || "",
        direction:       m.direction       || selectedSession.direction,
        timestamp: m.created_at?._seconds
          ? new Date(m.created_at._seconds * 1000).toISOString()
          : undefined,
      }));

      const result = await apiFetch("/summarize", token, {
        method: "POST",
        body: { conversation_time, output_language, messages: msgPayload },
      });

      setSelectedSession((prev) =>
        prev ? { ...prev, summary: result.summary, summary_status: "done" } : prev
      );
      setSessions((prev) =>
        prev.map((s) =>
          s.id === selectedSession.id
            ? { ...s, summary: result.summary, summary_status: "done" }
            : s
        )
      );
      if (result.analysis) setAnalysis(result.analysis);
    } catch (err) {
      console.error("Failed to generate summary:", err);
      setSelectedSession((prev) => prev ? { ...prev, summary_status: "error" } : prev);
    } finally {
      setGenerating(false);
    }
  }

  // ── Filtered list ─────────────────────────────────────────────────────────

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return sessions;
    return sessions.filter((s) =>
      (s.title ?? "").toLowerCase().includes(q)
    );
  }, [sessions, search]);

  // ── Render ────────────────────────────────────────────────────────────────

  const hasSummary = selectedSession?.summary && selectedSession.summary_status === "done";
  const isPending  = selectedSession?.summary_status === "pending";
  const isError    = selectedSession?.summary_status === "error";

  return (
    <div className="page-summaries">

      {/* ── Header ── */}
      <div className="sum-header">
        <div>
          <h1>Summaries</h1>
          <p>{sessions.length} session{sessions.length !== 1 ? "s" : ""}</p>
        </div>
        <div className="search-box">
          <i className="ti ti-search" style={{ fontSize: 13 }} />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search sessions…"
          />
        </div>
      </div>

      {/* ── Column headers ── */}
      <div className="sum-table-head">
        <div className="th" style={{ flex: 2 }}>Session</div>
        <div className="th">Direction</div>
        <div className="th">Last active</div>
        <div className="th" style={{ textAlign: "center" }}>Summary</div>
      </div>

      {/* ── Session rows ── */}
      {loadingSessions && <div className="sum-empty">Loading sessions…</div>}

      {!loadingSessions && filtered.length === 0 && (
        <div className="sum-empty">
          {sessions.length === 0 ? "No sessions with messages yet." : "No sessions match your search."}
        </div>
      )}

      {!loadingSessions && filtered.map((s) => (
        <button key={s.id} className="sum-row" onClick={() => openModal(s)}>
          <div style={{ flex: 2 }}>
            <div className="hr-title">{s.title || "Untitled session"}</div>
            {s.notes && <div className="hr-preview">{s.notes}</div>}
          </div>
          <div>
            <span className={`hr-dir ${s.direction === "de_to_en" ? "de" : "en"}`}>
              {dirLabel(s.direction)}
            </span>
          </div>
          <div className="hr-date">{formatDate(s.last_active)}</div>
          <div style={{ textAlign: "center" }}>
            <SummaryBadge status={s.summary_status} summary={s.summary} />
          </div>
        </button>
      ))}

      {/* ── Modal ── */}
      {selectedSession && (
        <div className="sum-modal-backdrop" onClick={closeModal}>
          <div className="sum-modal" onClick={(e) => e.stopPropagation()}>

            {/* Modal header */}
            <div className="sum-modal-head">
              <div>
                <div className="sum-modal-title">
                  {selectedSession.title || "Untitled session"}
                </div>
                <div className="preview-date">
                  {formatDate(selectedSession.last_active)} · {dirLabel(selectedSession.direction)}
                </div>
              </div>
              <button className="icon-btn" title="Close" onClick={closeModal}>
                <i className="ti ti-x" />
              </button>
            </div>

            {/* Analysis Metrics Box */}
            {analysis && (
              <div className="sum-metrics-box">
                <div className="sum-metrics-head">
                  <i className="ti ti-chart-bar" /> Analysis Metrics
                </div>
                <div className="sum-metrics-grid">
                  <div className="sum-metric-item">
                    <div className="sum-metric-label">Sentiment</div>
                    <span className={`sum-metric-badge ${analysis.sentiment.toLowerCase()}`}>
                      {analysis.sentiment}
                    </span>
                  </div>
                  <div className="sum-metric-item">
                    <div className="sum-metric-label">Formality</div>
                    <span className={`sum-metric-badge ${analysis.formality.toLowerCase()}`}>
                      {analysis.formality}
                    </span>
                  </div>
                  <div className="sum-metric-item">
                    <div className="sum-metric-label">Total Words</div>
                    <div className="sum-metric-value">{analysis.total_words}</div>
                  </div>
                  <div className="sum-metric-item">
                    <div className="sum-metric-label">Avg / Turn</div>
                    <div className="sum-metric-value">{analysis.avg_words_per_turn}</div>
                  </div>
                </div>
                <div className="sum-speakers-row">
                  <span className="sum-speaker-label">DE · {analysis.de_speaker_turns}</span>
                  <div className="sum-speaker-bar-wrap">
                    <div
                      className="sum-speaker-bar-de"
                      style={{
                        width: `${(analysis.de_speaker_turns / Math.max(analysis.de_speaker_turns + analysis.en_speaker_turns, 1)) * 100}%`,
                      }}
                    />
                  </div>
                  <span className="sum-speaker-label">{analysis.en_speaker_turns} · EN</span>
                </div>
                {analysis.main_topic && (
                  <div className="sum-main-topic">
                    <div className="sum-main-topic-label">Main Idea</div>
                    <div className="sum-main-topic-text">{analysis.main_topic}</div>
                  </div>
                )}
              </div>
            )}

            {/* Summary section */}
            <div className="sum-modal-summary">
              <div className="sum-summary-head">
                <div className="notes-label">Summary</div>
                <div style={{ display: "flex", gap: 6 }}>
                  {hasSummary && (
                    <button
                      className="icon-btn"
                      title="Regenerate summary"
                      onClick={() => triggerSummary()}
                      disabled={generating || loadingMessages}
                    >
                      <i className={`ti ti-refresh${generating ? " spin" : ""}`} />
                    </button>
                  )}
                  {!hasSummary && !isPending && (
                    <button
                      className="sum-generate-btn"
                      onClick={() => triggerSummary()}
                      disabled={generating || loadingMessages}
                    >
                      <i className={`ti ti-sparkles${generating ? " spin" : ""}`} />
                      {loadingMessages ? "Loading…" : "Generate"}
                    </button>
                  )}
                </div>
              </div>

              {isPending && (
                <p className="sum-generating">
                  <i className="ti ti-loader-2 spin" /> Generating summary… (may take several minutes on CPU)
                </p>
              )}
              {hasSummary && (
                <p className="sum-text">{selectedSession.summary}</p>
              )}
              {isError && !isPending && (
                <p className="sum-error">
                  Generation failed.{" "}
                  <button className="sum-retry-link" onClick={() => triggerSummary(true)}>
                    Try again
                  </button>
                </p>
              )}
              {!hasSummary && !isPending && !isError && (
                <p className="sum-none">No summary yet — click Generate to create one.</p>
              )}
            </div>

            {/* Transcript */}
            <div className="sum-modal-body">
              {loadingMessages && <div className="sum-empty">Loading transcript…</div>}
              {!loadingMessages && messages.length === 0 && (
                <div className="sum-empty">No transcript messages.</div>
              )}
              {!loadingMessages && messages.map((m, i) => {
                const srcLabel = selectedSession.direction === "de_to_en" ? "German" : "English";
                const tgtLabel = selectedSession.direction === "de_to_en" ? "English" : "German";
                return (
                  <div key={m.id ?? i} className="sum-msg-pair">
                    <div className="pmsg src">
                      <div className="pmsg-lang">{srcLabel}</div>
                      {m.source_text}
                    </div>
                    <div className="pmsg tr" style={{ marginTop: 6 }}>
                      <div className="pmsg-lang">{tgtLabel}</div>
                      {m.translated_text}
                    </div>
                  </div>
                );
              })}
            </div>

          </div>
        </div>
      )}
    </div>
  );
}
