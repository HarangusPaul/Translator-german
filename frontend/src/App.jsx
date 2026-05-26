import { useEffect, useState } from "react";
import { useAuth } from "./contexts/AuthContext";
import Nav from "./components/Nav";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import TranslatorPage from "./pages/TranslatorPage";
import History from "./pages/History";
import Settings from "./pages/Settings";
import Summaries from "./pages/Summaries";

export default function App() {
  const { user, loading } = useAuth();
  const [activePage, setActivePage] = useState("login");
  const [currentSessionId, setCurrentSessionId] = useState(null);

  useEffect(() => {
    if (localStorage.getItem("darkMode") === "true") {
      document.documentElement.setAttribute("data-theme", "dark");
    }
  }, []);

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

  function openSession(id) {
    setCurrentSessionId(id);
    setActivePage("translator");
  }

  function startNewSession() {
    setCurrentSessionId(null);
    setActivePage("translator");
  }

  function endSession() {
    setCurrentSessionId(null);
    setActivePage("dashboard");
  }

  return (
    <div className="app">
      <Nav activePage={activePage} setActivePage={goTo} />
      {!user && <Login onSuccess={() => goTo("dashboard")} />}
      {user && activePage === "dashboard" && (
        <Dashboard onNewSession={startNewSession} onOpenSession={openSession} />
      )}
      {user && activePage === "translator" && (
        <TranslatorPage sessionId={currentSessionId} onEnd={endSession} />
      )}
      {user && activePage === "history" && (
        <History onOpenSession={openSession} />
      )}
      {user && activePage === "summaries" && <Summaries />}
      {user && activePage === "settings" && <Settings />}
    </div>
  );
}
