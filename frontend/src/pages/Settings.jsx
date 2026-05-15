import { useState } from "react";
import { useAuth } from "../contexts/AuthContext";

function Toggle({ on, onToggle }) {
  return <div className={`toggle${on ? "" : " off"}`} onClick={onToggle} />;
}

export default function Settings() {
  const { user, logout } = useAuth();
  const [activeNav, setActiveNav] = useState("Profile");
  const [prefs, setPrefs] = useState({ defaultDir: "DE → EN", tts: true, partial: true, asrModel: "large-v3", autoSave: true });

  const toggle = (key) => setPrefs((p) => ({ ...p, [key]: !p[key] }));

  const navItems = [
    { label: "Profile", icon: "ti-user" },
    { label: "Translation", icon: "ti-language" },
    { label: "Audio", icon: "ti-microphone" },
    { label: "Notifications", icon: "ti-bell" },
    { label: "Security", icon: "ti-lock" },
  ];

  function initials() {
    if (user?.displayName) return user.displayName.split(" ").map((w) => w[0]).join("").slice(0, 2).toUpperCase();
    return user?.email?.[0]?.toUpperCase() ?? "?";
  }

  return (
    <div className="page-settings">
      <div className="settings-nav">
        <h2>Settings</h2>
        {navItems.map((n) => (
          <div key={n.label} className={`snav-item${activeNav === n.label ? " active" : ""}`} onClick={() => setActiveNav(n.label)}>
            <i className={`ti ${n.icon}`} /> {n.label}
          </div>
        ))}
      </div>

      <div className="settings-body">
        <h2>{activeNav}</h2>

        {activeNav === "Profile" && (
          <>
            <div className="profile-card">
              <div className="av-lg">{initials()}</div>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 14, fontWeight: 500 }}>{user?.displayName ?? "User"}</div>
                <div style={{ fontSize: 12, color: "var(--text-faint)", marginTop: 2 }}>{user?.email}</div>
                <div style={{ fontSize: 11, color: "var(--text-faint)", marginTop: 2 }}>
                  {user?.providerData?.[0]?.providerId === "google.com" ? "Signed in with Google" : "Email account"}
                </div>
              </div>
              <button className="icon-btn" title="Edit"><i className="ti ti-edit" /></button>
            </div>

            <div className="setting-group">
              <h3>Translation preferences</h3>
              <div className="setting-row">
                <div><div className="setting-label">Default direction</div><div className="setting-desc">Which language you hear most</div></div>
                <select className="setting-select" value={prefs.defaultDir} onChange={(e) => setPrefs((p) => ({ ...p, defaultDir: e.target.value }))}>
                  <option>DE → EN</option>
                  <option>EN → DE</option>
                </select>
              </div>
              <div className="setting-row">
                <div><div className="setting-label">TTS playback</div><div className="setting-desc">Read translations aloud automatically</div></div>
                <Toggle on={prefs.tts} onToggle={() => toggle("tts")} />
              </div>
              <div className="setting-row">
                <div><div className="setting-label">Partial transcription</div><div className="setting-desc">Show words as they are spoken</div></div>
                <Toggle on={prefs.partial} onToggle={() => toggle("partial")} />
              </div>
              <div className="setting-row">
                <div><div className="setting-label">ASR model</div><div className="setting-desc">Larger = more accurate, slower</div></div>
                <select className="setting-select" value={prefs.asrModel} onChange={(e) => setPrefs((p) => ({ ...p, asrModel: e.target.value }))}>
                  <option>large-v3</option>
                  <option>medium</option>
                  <option>small</option>
                </select>
              </div>
            </div>

            <div className="setting-group">
              <h3>Data</h3>
              <div className="setting-row">
                <div><div className="setting-label">Auto-save sessions</div><div className="setting-desc">Save to Firestore after each message</div></div>
                <Toggle on={prefs.autoSave} onToggle={() => toggle("autoSave")} />
              </div>
              <div className="setting-row">
                <div><div className="setting-label">Sign out</div><div className="setting-desc">Sign out of your account</div></div>
                <button className="danger-btn" onClick={logout}>Sign out</button>
              </div>
              <div className="setting-row">
                <div><div className="setting-label">Delete all sessions</div><div className="setting-desc">Permanently removes all history</div></div>
                <button className="danger-btn">Delete all</button>
              </div>
            </div>
          </>
        )}

        {activeNav !== "Profile" && (
          <div style={{ color: "var(--text-faint)", fontSize: 13, paddingTop: 8 }}>
            {activeNav} settings coming soon.
          </div>
        )}
      </div>
    </div>
  );
}
