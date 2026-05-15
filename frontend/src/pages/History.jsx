import { useState } from "react";

const SESSIONS = [
  { id: 1, title: "Doctor appointment", preview: "Guten Tag, ich habe einen Termin…", dir: "DE→EN", date: "Today, 14:32", msgs: 12 },
  { id: 2, title: "Hotel check-in", preview: "Where is my room key…", dir: "EN→DE", date: "Yesterday, 09:15", msgs: 8 },
  { id: 3, title: "Business meeting", preview: "Die Quartalsberichte zeigen…", dir: "DE→EN", date: "May 12, 11:00", msgs: 18 },
  { id: 4, title: "Airport help desk", preview: "My flight was delayed…", dir: "EN→DE", date: "May 10, 16:40", msgs: 6 },
  { id: 5, title: "Restaurant order", preview: "Ich möchte bitte bestellen…", dir: "DE→EN", date: "May 9, 20:12", msgs: 4 },
];

export default function History() {
  const [filter, setFilter] = useState("All");
  const [search, setSearch] = useState("");

  const filters = ["All", "DE→EN", "EN→DE", "This week"];

  const rows = SESSIONS.filter((s) => {
    if (filter === "DE→EN" && s.dir !== "DE→EN") return false;
    if (filter === "EN→DE" && s.dir !== "EN→DE") return false;
    if (filter === "This week" && !["Today", "Yesterday"].some((d) => s.date.startsWith(d))) return false;
    if (search && !s.title.toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  });

  const total = SESSIONS.reduce((a, s) => a + s.msgs, 0);

  return (
    <div className="page-history">
      <div className="hist-header">
        <div>
          <h1>Session history</h1>
          <p>{SESSIONS.length} sessions · {total} messages total</p>
        </div>
        <button className="icon-btn" title="Export all"><i className="ti ti-download" /></button>
      </div>

      <div className="hist-filter-row">
        {filters.map((f) => (
          <button key={f} className={`hf-pill${filter === f ? " active" : ""}`} onClick={() => setFilter(f)}>{f}</button>
        ))}
        <div className="search-box">
          <i className="ti ti-search" style={{ fontSize: 13 }} />
          <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search sessions…" />
        </div>
      </div>

      <div className="hist-table-head">
        <div className="th">Session</div>
        <div className="th">Direction</div>
        <div className="th">Date</div>
        <div className="th" style={{ textAlign: "right" }}>Messages</div>
      </div>

      {rows.map((s) => (
        <div key={s.id} className="hist-row">
          <div>
            <div className="hr-title">{s.title}</div>
            <div className="hr-preview">{s.preview}</div>
          </div>
          <div>
            <span className={`hr-dir ${s.dir === "DE→EN" ? "de" : "en"}`}>{s.dir}</span>
          </div>
          <div className="hr-date">{s.date}</div>
          <div className="hr-actions">
            <span className="hr-count">{s.msgs}</span>
            <button className="icon-btn" title="Open"><i className="ti ti-player-play" /></button>
            <button className="icon-btn" title="Delete"><i className="ti ti-trash" /></button>
          </div>
        </div>
      ))}

      {rows.length === 0 && (
        <div style={{ padding: "32px 24px", textAlign: "center", color: "var(--text-faint)", fontSize: 13 }}>
          No sessions match your filter.
        </div>
      )}
    </div>
  );
}
