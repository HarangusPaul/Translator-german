import useTranslation from "../hooks/useTranslation";

/**
 * TranslationPanel — wraps useTranslation and renders a live two-column
 * transcript / translation view.
 *
 * Props (unchanged from original):
 *   direction  "de_to_en" | "en_to_de"
 *   title      Panel heading
 *   subtitle   Subheading text
 *   srcLabel   Label above the source (transcript) textarea
 *   tgtLabel   Label above the target (translation) textarea
 *   playTTS    Whether to auto-play received TTS audio
 *
 * Added prop:
 *   sessionId  Firestore session ID for persistence (optional, default null)
 */
export default function TranslationPanel({
  direction,
  title,
  subtitle,
  srcLabel,
  tgtLabel,
  playTTS,
  sessionId = null,
}) {
  const {
    status,
    partialTranscript,
    finalTranscript,
    partialTranslation,
    finalTranslation,
    start,
    stop,
    clear,
  } = useTranslation(sessionId, direction, playTTS);

  const isActive = status === "live";
  const isConnecting = status === "connecting";
  const isError = status === "error";

  return (
    <section
      className={`panel${isActive ? " panel--active" : ""}${isError ? " panel--error" : ""}`}
    >
      {/* ── Header ─────────────────────────────────────────────────────── */}
      <div className="panel-header">
        <div>
          <h2 className="panel-title">{title}</h2>
          <span className="panel-subtitle">{subtitle}</span>
        </div>
        <div className="status-badge-wrap">
          {isConnecting && (
            <span className="badge badge--connecting">Connecting…</span>
          )}
          {isActive && <span className="badge badge--active">● Live</span>}
          {isError && <span className="badge badge--error">Error</span>}
        </div>
      </div>

      {/* ── Controls ───────────────────────────────────────────────────── */}
      <div className="panel-controls">
        {!isActive && !isConnecting ? (
          <>
            <button className="btn btn--primary" onClick={() => start(false)}>
              🎤 Microphone
            </button>
            <button className="btn btn--secondary" onClick={() => start(true)}>
              🖥️ Share Tab Audio
            </button>
          </>
        ) : (
          <button className="btn btn--danger" onClick={stop}>
            ⏹ Stop
          </button>
        )}
        <button
          className="btn btn--ghost"
          onClick={clear}
          disabled={isActive || isConnecting}
        >
          Clear
        </button>
      </div>

      {/* ── Text areas ─────────────────────────────────────────────────── */}
      <div className="text-areas">
        {/* Source — transcript */}
        <div className="textarea-wrapper">
          <label className="textarea-label">{srcLabel}</label>
          <textarea
            className="textarea"
            value={finalTranscript}
            readOnly
            placeholder="Transcript will appear here…"
          />
          {partialTranscript ? (
            <p className="partial-text">{partialTranscript}</p>
          ) : null}
        </div>

        {/* Target — translation */}
        <div className="textarea-wrapper">
          <label className="textarea-label">{tgtLabel}</label>
          <textarea
            className="textarea textarea--translated"
            value={finalTranslation}
            readOnly
            placeholder="Translation will appear here…"
          />
          {partialTranslation ? (
            <p className="partial-text partial-text--translated">
              {partialTranslation}
            </p>
          ) : null}
        </div>
      </div>
    </section>
  );
}
