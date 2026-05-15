import useTranslation from "../hooks/useTranslation";

const WAVE_BARS = 14;

function WaveBar({ active, index }) {
  const heights = [8, 16, 22, 18, 26, 14, 20, 10, 24, 16, 8, 18, 22, 12];
  return (
    <div
      className={`wb ${active ? "on" : "off"}`}
      style={{ height: active ? heights[index % heights.length] : 4 }}
    />
  );
}

function Panel({ direction, langLabel, roleLabel, flag, sessionId, playTTS }) {
  const { status, partialTranscript, finalTranscript, partialTranslation, finalTranslation, start, stop, clear } = useTranslation(sessionId, direction, playTTS);
  const isLive = status === "live";
  const isConnecting = status === "connecting";

  const srcLang = direction === "de_to_en" ? "German" : "English";
  const tgtLang = direction === "de_to_en" ? "English" : "German";

  const lines = [];
  finalTranscript.split("\n").filter(Boolean).forEach((src, i) => {
    const tr = finalTranslation.split("\n")[i] ?? "";
    lines.push({ src, tr });
  });

  return (
    <div className="t-panel">
      <div className="ph">
        <div>
          <div className="ph-lang">{flag} {langLabel}</div>
          <div className="ph-role">{roleLabel}</div>
        </div>
        <div className="src-tog">
          <button className={`stb${!isLive && !isConnecting ? "" : " on"}`} onClick={() => isLive ? stop() : start(false)}>
            <i className="ti ti-microphone" /> Mic
          </button>
          <button className="stb" onClick={() => isLive ? stop() : start(true)}>
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
              {isConnecting ? "connecting…" : "press mic to start"}
            </span>
            <div className="waiting-line" />
          </div>
        )}
      </div>

      <div className="pf">
        <button className={`rrb ${isLive ? "live" : "idle"}`} onClick={() => isLive ? stop() : start(false)}>
          <i className={`ti ${isLive ? "ti-player-stop" : "ti-microphone"}`} />
        </button>
        <div className="wave">
          {Array.from({ length: WAVE_BARS }, (_, i) => (
            <WaveBar key={i} active={isLive} index={i} />
          ))}
        </div>
        <button className="icon-btn" title="Clear" onClick={clear} disabled={isLive}>
          <i className="ti ti-eraser" />
        </button>
      </div>
    </div>
  );
}

export default function TranslatorPage({ sessionId = null }) {
  return (
    <div className="page-translator">
      <div className="tbar">
        <div className="session-chip">
          <i className="ti ti-folder" style={{ fontSize: 13 }} />
          Session: <span>{sessionId ?? "New session"}</span>
        </div>
        <div className="live-chip">
          <div className="rdot" /> Live · 2 panels
        </div>
        <div className="dir-toggle">
          <button className="dt-opt sel">DE → EN</button>
          <button className="dt-opt">EN → DE</button>
        </div>
        <button className="icon-btn" title="Save"><i className="ti ti-device-floppy" /></button>
        <button className="icon-btn" title="End session"><i className="ti ti-x" /></button>
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
          <button className="icon-btn" title="Download"><i className="ti ti-download" /></button>
          <button className="icon-btn" title="Share"><i className="ti ti-share" /></button>
        </div>
      </div>
    </div>
  );
}
