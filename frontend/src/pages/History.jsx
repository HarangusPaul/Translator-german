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
  if (diff < 86_400_000) return `Today, ${time}`;
  if (diff < 172_800_000) return `Yesterday, ${time}`;
  return d.toLocaleDateString([], { month: "short", day: "numeric" }) + `, ${time}`;
}

function isThisWeek(ts) {
  if (!ts) return false;
  const d = ts?._seconds ? new Date(ts._seconds * 1000) : new Date(ts);
  return !isNaN(d) && (new Date() - d) < 7 * 86_400_000;
}

export default function History({ onOpenSession }) {
  const { getFreshToken } = useAuth();
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("All");
  const [search, setSearch] = useState("");

  useEffect(() => {
    async function load() {
      try {
        const token = await getFreshToken();
        const data = await apiFetch("/sessions", token);
        setSessions(data);
      } catch (err) {
        console.error("Failed to load sessions:", err);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [getFreshToken]);

  async function deleteSession(id) {
    try {
      const token = await getFreshToken();
      await apiFetch(`/sessions/${id}`, token, { method: "DELETE" });
      setSessions((prev) => prev.filter((s) => s.id !== id));
    } catch (err) {
      console.error("Delete failed:", err);
    }
  }

  const filters = ["All", "DE→EN", "EN→DE", "This week"];

  const rows = sessions.filter((s) => {
    const dirStr = s.direction === "de_to_en" ? "DE→EN" : "EN→DE";
    if (filter === "DE→EN" && dirStr !== "DE→EN") return false;
    if (filter === "EN→DE" && dirStr !== "EN→DE") return false;
    if (filter === "This week" && !isThisWeek(s.last_active)) return false;
    if (search && !s.title.toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  });

  return (
    <div className="page-history">
      <div className="hist-header">
        <div>
          <h1>Session history</h1>
          <p>{sessions.length} sessions total</p>
        </div>
      </div>

      <div className="hist-filter-row">
        {filters.map((f) => (
          <button
            key={f}
            className={`hf-pill${filter === f ? " active" : ""}`}
            onClick={() => setFilter(f)}
          >
            {f}
          </button>
        ))}
        <div className="search-box">
          <i className="ti ti-search" style={{ fontSize: 13 }} />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search sessions…"
          />
        </div>
      </div>

      <div className="hist-table-head">
        <div className="th">Session</div>
        <div className="th">Direction</div>
        <div className="th">Date</div>
        <div className="th" style={{ textAlign: "right" }}>Actions</div>
      </div>

      {loading && (
        <div style={{ padding: "32px 24px", textAlign: "center", color: "var(--text-faint)", fontSize: 13 }}>
          Loading…
        </div>
      )}

      {!loading && rows.map((s) => {
        const dirStr = s.direction === "de_to_en" ? "DE→EN" : "EN→DE";
        return (
          <div key={s.id} className="hist-row">
            <div>
              <div className="hr-title">{s.title}</div>
              {s.notes && <div className="hr-preview">{s.notes}</div>}
            </div>
            <div>
              <span className={`hr-dir ${s.direction === "de_to_en" ? "de" : "en"}`}>{dirStr}</span>
            </div>
            <div className="hr-date">{formatDate(s.last_active)}</div>
            <div className="hr-actions">
              <button className="icon-btn" title="Open" onClick={() => onOpenSession(s.id)}>
                <i className="ti ti-player-play" />
              </button>
              <button className="icon-btn" title="Delete" onClick={() => deleteSession(s.id)}>
                <i className="ti ti-trash" />
              </button>
            </div>
          </div>
        );
      })}

      {!loading && rows.length === 0 && (
        <div style={{ padding: "32px 24px", textAlign: "center", color: "var(--text-faint)", fontSize: 13 }}>
          {sessions.length === 0 ? "No sessions yet. Start a new one!" : "No sessions match your filter."}
        </div>
      )}
    </div>
  );
}
