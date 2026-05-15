import { useEffect, useRef, useState } from "react";
import { useAuth } from "../contexts/AuthContext";
import { apiFetch } from "../api";
import useTranslation from "../hooks/useTranslation";

const WAVE_BARS = 14;
const WAVE_HEIGHTS = [8, 16, 22, 18, 26, 14, 20, 10, 24, 16, 8, 18, 22, 12];

function WaveBar({ active, index }) {
  return (
    <div
      className={`wb ${active ? "on" : "off"}`}
      style={{ height: active ? WAVE_HEIGHTS[index % WAVE_HEIGHTS.length] : 4 }}
    />
  );
}

function Panel({ direction, langLabel, roleLabel, flag, sessionId, playTTS }) {
  const { status, partialTranscript, finalTranscript, partialTranslation, finalTranslation, start, stop, clear } =
    useTranslation(sessionId, direction, playTTS);

  const isLive = status === "live";
  const isConnecting = status === "connecting";
  const isError = status === "error";

  const srcLang = direction === "de_to_en" ? "German" : "English";
  const tgtLang = direction === "de_to_en" ? "English" : "German";

  const lines = [];
  const srcLines = finalTranscript.split("\n").filter(Boolean);
  const trLines = finalTranslation.split("\n");
  srcLines.forEach((src, i) => lines.push({ src, tr: trLines[i] ?? "" }));

  const bottomRef = useRef(null);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [lines.length, partialTranscript]);

  return (
    <div className="t-panel">
      <div className="ph">
        <div>
          <div className="ph-lang">{flag} {langLabel}</div>
          <div className="ph-role">
            {isError ? "Connection error" : isConnecting ? "Connecting…" : isLive ? "● Live" : roleLabel}
          </div>
        </div>
        <div className="src-tog">
          <button
            className={`stb${isLive ? " on" : ""}`}
            onClick={() => (isLive ? stop() : start(false))}
            disabled={isConnecting}
          >
            <i className="ti ti-microphone" /> Mic
          </button>
          <button
            className="stb"
            onClick={() => (isLive ? stop() : start(true))}
            disabled={isConnecting}
          >
            <i className="ti ti-screen-share" /> Tab
          </button>
        </div>
      </div>

      <div className="ta">
        {lines.map((line, i) => (
          <div key={i}>
            <div className="bbl src">
              <div className="bbl-lbl">{srcLang}</div>
              <div className="bbl-txt">{line.src}</div>
            </div>
            {line.tr && (
              <div className="bbl trl" style={{ marginTop: 6 }}>
                <div className="bbl-lbl">{tgtLang}</div>
                <div className="bbl-txt">{line.tr}</div>
              </div>
            )}
          </div>
        ))}

        {partialTranscript && (
          <div className="bbl src partial">
            <div className="bbl-lbl">{srcLang} · live</div>
            <div className="bbl-txt">{partialTranscript}</div>
          </div>
        )}
        {partialTranslation && (
          <div className="bbl trl partial">
            <div className="bbl-lbl">{tgtLang} · translating</div>
            <div className="bbl-txt">{partialTranslation}</div>
          </div>
        )}

        {!isLive && lines.length === 0 && !partialTranscript && (
          <div className="waiting-row">
            <div className="waiting-line" />
            <span className="waiting-text">
              {isConnecting ? "connecting…" : isError ? "error — retry mic" : "press mic to start"}
            </span>
            <div className="waiting-line" />
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="pf">
        <button
          className={`rrb ${isLive ? "live" : "idle"}`}
          onClick={() => (isLive ? stop() : start(false))}
          disabled={isConnecting}
        >
          <i className={`ti ${isLive ? "ti-player-stop" : "ti-microphone"}`} />
        </button>
        <div className="wave">
          {Array.from({ length: WAVE_BARS }, (_, i) => (
            <WaveBar key={i} active={isLive} index={i} />
          ))}
        </div>
        <button className="icon-btn" title="Clear" onClick={clear} disabled={isLive || isConnecting}>
          <i className="ti ti-eraser" />
        </button>
      </div>
    </div>
  );
}

function autoTitle() {
  const now = new Date();
  return `Session ${now.toLocaleDateString([], { month: "short", day: "numeric" })} ${now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
}

export default function TranslatorPage({ sessionId: propSessionId = null, onEnd }) {
  const { getFreshToken } = useAuth();
  const [sessionId, setSessionId] = useState(propSessionId);
  const [direction, setDirection] = useState("de_to_en");
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    if (propSessionId) {
      setSessionId(propSessionId);
      return;
    }
    // Create a new session automatically
    setCreating(true);
    async function create() {
      try {
        const token = await getFreshToken();
        const result = await apiFetch("/sessions", token, {
          method: "POST",
          body: { title: autoTitle(), direction, notes: "" },
        });
        setSessionId(result.id);
      } catch (err) {
        console.error("Session creation failed:", err);
      } finally {
        setCreating(false);
      }
    }
    create();
  }, []);  // intentionally run once on mount

  return (
    <div className="page-translator">
      <div className="tbar">
        <div className="session-chip">
          <i className="ti ti-folder" style={{ fontSize: 13 }} />
          {creating ? "Creating session…" : sessionId ? `Session: ${sessionId.slice(0, 8)}…` : "New session"}
        </div>
        <div className="live-chip">
          <div className="rdot" /> Live · 2 panels
        </div>
        <div className="dir-toggle">
          <button
            className={`dt-opt${direction === "de_to_en" ? " sel" : ""}`}
            onClick={() => setDirection("de_to_en")}
          >
            DE → EN
          </button>
          <button
            className={`dt-opt${direction === "en_to_de" ? " sel" : ""}`}
            onClick={() => setDirection("en_to_de")}
          >
            EN → DE
          </button>
        </div>
        <button className="icon-btn" title="End session" onClick={onEnd}>
          <i className="ti ti-x" />
        </button>
      </div>

      <div className="panels">
        <Panel
          direction="de_to_en"
          langLabel="German speaker"
          roleLabel="Incoming · TTS off"
          flag="🇩🇪"
          sessionId={sessionId}
          playTTS={false}
        />
        <Panel
          direction="en_to_de"
          langLabel="Your response"
          roleLabel="EN → DE · TTS on"
          flag="🇬🇧"
          sessionId={sessionId}
          playTTS={true}
        />
      </div>

      <div className="tbot">
        <div className="model-tag">
          <i className="ti ti-cpu" style={{ fontSize: 13 }} />
          Whisper large-v3 · MarianMT · Edge TTS · 800ms partial
        </div>
        <div className="tbot-right">
          <button className="icon-btn" title="End session" onClick={onEnd}>
            <i className="ti ti-door-exit" />
          </button>
        </div>
      </div>
    </div>
  );
}
