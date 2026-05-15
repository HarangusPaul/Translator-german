import { useEffect, useState } from "react";
import { useAuth } from "./contexts/AuthContext";
import Nav from "./components/Nav";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import TranslatorPage from "./pages/TranslatorPage";
import History from "./pages/History";
import Settings from "./pages/Settings";

export default function App() {
  const { user, loading } = useAuth();
  const [activePage, setActivePage] = useState("login");

  useEffect(() => {
    if (!loading) {
      setActivePage(user ? "dashboard" : "login");
    }
  }, [user, loading]);

  if (loading) {
    return (
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", minHeight: "100vh", color: "var(--text-muted)", fontSize: 13 }}>
        Loading…
      </div>
    );
  }

  function goTo(page) {
    setActivePage(page);
  }

  return (
    <div className="app">
      <Nav activePage={activePage} setActivePage={goTo} />
      {!user && <Login onSuccess={() => goTo("dashboard")} />}
      {user && activePage === "dashboard" && <Dashboard onNewSession={() => goTo("translator")} />}
      {user && activePage === "translator" && <TranslatorPage />}
      {user && activePage === "history" && <History />}
      {user && activePage === "settings" && <Settings />}
    </div>
  );
}
