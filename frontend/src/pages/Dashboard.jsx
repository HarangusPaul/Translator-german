import { useState } from "react";
import { useAuth } from "../contexts/AuthContext";

const SESSIONS = [
  { id: 1, title: "Doctor appointment", icon: "ti-stethoscope", type: "de", dir: "DE→EN", date: "Today · 14:32", msgs: 12, preview: "Guten Tag, ich habe einen Termin bei Dr. Müller.", notes: "Checkup at Dr. Müller's clinic, insurance needed", transcript: [{ src: "Guten Tag, ich habe einen Termin bei Dr. Müller.", tr: "Good afternoon, I have an appointment with Dr. Müller.", t: "14:32" }, { src: "Können Sie Ihren Versicherungsausweis zeigen?", tr: "Can you show your insurance card?", t: "14:33" }] },
  { id: 2, title: "Hotel check-in", icon: "ti-building", type: "en", dir: "EN→DE", date: "Yesterday · 09:15", msgs: 8, preview: "Where is my room key…", notes: "", transcript: [{ src: "Where is my room key?", tr: "Wo ist mein Zimmerschlüssel?", t: "09:15" }] },
  { id: 3, title: "Business meeting", icon: "ti-briefcase", type: "de", dir: "DE→EN", date: "May 12 · 11:00", msgs: 18, preview: "Die Quartalsberichte zeigen…", notes: "Q2 review with Berlin office", transcript: [{ src: "Die Quartalsberichte zeigen eine positive Entwicklung.", tr: "The quarterly reports show a positive development.", t: "11:00" }] },
  { id: 4, title: "Airport help desk", icon: "ti-plane", type: "en", dir: "EN→DE", date: "May 10 · 16:40", msgs: 6, preview: "My flight was delayed…", notes: "", transcript: [{ src: "My flight was delayed by two hours.", tr: "Mein Flug hatte zwei Stunden Verspätung.", t: "16:40" }] },
  { id: 5, title: "Restaurant order", icon: "ti-tools-kitchen-2", type: "de", dir: "DE→EN", date: "May 9 · 20:12", msgs: 4, preview: "Ich möchte bitte bestellen…", notes: "", transcript: [{ src: "Ich möchte bitte bestellen.", tr: "I would like to order, please.", t: "20:12" }] },
];

export default function Dashboard({ onNewSession }) {
  const { user } = useAuth();
  const [selected, setSelected] = useState(SESSIONS[0]);

  const firstName = user?.displayName?.split(" ")[0] ?? user?.email?.split("@")[0] ?? "there";
  const hour = new Date().getHours();
  const greeting = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";

  const totalMsgs = SESSIONS.reduce((s, x) => s + x.msgs, 0);
  const deCount = SESSIONS.filter((s) => s.dir === "DE→EN").reduce((a, s) => a + s.msgs, 0);
  const enCount = totalMsgs - deCount;

  return (
    <div className="page-dashboard">
      <div className="dash-header">
        <div>
          <h1>{greeting}, {firstName}</h1>
          <p>{SESSIONS.length} sessions · {totalMsgs} messages translated</p>
        </div>
        <button className="btn-main" onClick={onNewSession}>
          <i className="ti ti-plus" /> New session
        </button>
      </div>

      <div className="stats-row">
        <div className="stat-card"><div className="stat-label">Total sessions</div><div className="stat-val">{SESSIONS.length}</div><div className="stat-sub">+2 this week</div></div>
        <div className="stat-card"><div className="stat-label">Messages</div><div className="stat-val">{totalMsgs}</div><div className="stat-sub">avg {(totalMsgs / SESSIONS.length).toFixed(1)} per session</div></div>
        <div className="stat-card"><div className="stat-label">DE→EN</div><div className="stat-val">{deCount}</div><div className="stat-sub">{Math.round(deCount / totalMsgs * 100)}% of messages</div></div>
        <div className="stat-card"><div className="stat-label">EN→DE</div><div className="stat-val">{enCount}</div><div className="stat-sub">{Math.round(enCount / totalMsgs * 100)}% of messages</div></div>
      </div>

      <div className="dash-body">
        <div className="session-col">
          <div className="col-head">Recent sessions</div>
          {SESSIONS.map((s) => (
            <div key={s.id} className={`srow${selected?.id === s.id ? " active" : ""}`} onClick={() => setSelected(s)}>
              <div className={`srow-icon ${s.type}`}><i className={`ti ${s.icon}`} /></div>
              <div className="srow-info">
                <div className="srow-title">{s.title}</div>
                <div className="srow-meta">{s.date} · {s.msgs} messages</div>
              </div>
              <span className={`dir-pill ${s.type}`}>{s.dir}</span>
            </div>
          ))}
        </div>

        <div className="preview-col">
          {selected ? (
            <>
              <div className="preview-head">
                <div>
                  <div className="preview-title">{selected.title}</div>
                  <div className="preview-date">{selected.date} · {selected.dir}</div>
                </div>
                <div className="preview-actions">
                  <button className="icon-btn" title="Open" onClick={onNewSession}><i className="ti ti-player-play" /></button>
                  <button className="icon-btn" title="Export"><i className="ti ti-download" /></button>
                  <button className="icon-btn" title="Delete"><i className="ti ti-trash" /></button>
                </div>
              </div>
              {selected.notes && (
                <div className="notes-box">
                  <div className="notes-label">Notes</div>
                  {selected.notes}
                </div>
              )}
              <div className="preview-msgs">
                {selected.transcript.map((m, i) => (
                  <div key={i}>
                    <div className="pmsg src"><div className="pmsg-lang">{selected.dir.split("→")[0] === "DE" ? "German" : "English"}</div>{m.src}<div className="pmsg-time">{m.t}</div></div>
                    <div className="pmsg tr" style={{ marginTop: 6 }}><div className="pmsg-lang">{selected.dir.split("→")[1] === "EN" ? "English" : "German"}</div>{m.tr}<div className="pmsg-time">{m.t}</div></div>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <div className="empty-preview">
              <i className="ti ti-message-2" />
              Select a session to preview
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
