import { useAuth } from "../contexts/AuthContext";

const TABS = [
  { id: "login", icon: "ti-lock", label: "Login", authOnly: false, guestOnly: true },
  { id: "dashboard", icon: "ti-layout-dashboard", label: "Dashboard", authOnly: true },
  { id: "translator", icon: "ti-microphone", label: "Translator", authOnly: true },
  { id: "history", icon: "ti-history", label: "History", authOnly: true },
  { id: "settings", icon: "ti-settings", label: "Settings", authOnly: true },
];

function initials(user) {
  if (!user) return "?";
  if (user.displayName) return user.displayName.split(" ").map((w) => w[0]).join("").slice(0, 2).toUpperCase();
  return user.email?.[0]?.toUpperCase() ?? "?";
}

export default function Nav({ activePage, setActivePage }) {
  const { user } = useAuth();

  const visibleTabs = TABS.filter((t) => {
    if (t.authOnly && !user) return false;
    if (t.guestOnly && user) return false;
    return true;
  });

  return (
    <nav className="nav">
      <div className="nav-logo">
        <div className="logo-sq"><i className="ti ti-language" /></div>
        <div>
          <div className="logo-wordmark">Übersetzer</div>
          <div className="logo-sub">DE ↔ EN</div>
        </div>
      </div>
      <div className="nav-tabs">
        {visibleTabs.map((t) => (
          <button
            key={t.id}
            className={`ntab${activePage === t.id ? " active" : ""}`}
            onClick={() => setActivePage(t.id)}
          >
            <i className={`ti ${t.icon}`} />
            {t.label}
          </button>
        ))}
      </div>
      {user && (
        <div className="nav-end">
          <div className="av" title={user.email}>{initials(user)}</div>
        </div>
      )}
    </nav>
  );
}
