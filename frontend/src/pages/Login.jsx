import { useState } from "react";
import { useAuth } from "../contexts/AuthContext";

export default function Login({ onSuccess }) {
  const { login, loginWithGoogle, register } = useAuth();
  const [mode, setMode] = useState("signin"); // signin | signup
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);


  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      if (mode === "signup") {
        await register(email, password, name);
      } else {
        await login(email, password);
      }
      onSuccess?.();
    } catch (err) {
      setError(err.message.replace("Firebase: ", "").replace(/\(auth\/.*\)\.?/, "").trim());
    } finally {
      setLoading(false);
    }
  }

  async function handleGoogle() {
    setError("");
    try {
      await loginWithGoogle();
      onSuccess?.();
    } catch (err) {
      setError(err.message.replace("Firebase: ", "").replace(/\(auth\/.*\)\.?/, "").trim());
    }
  }

  return (
    <div className="page-login">
      <div className="login-left">
        <div>
          <div className="logo-sq" style={{ width: 40, height: 40, marginBottom: 16 }}>
            <i className="ti ti-language" style={{ fontSize: 20 }} />
          </div>
          <div style={{ fontSize: 13, color: "#5DCAA5" }}>Übersetzer — real-time translator</div>
        </div>
        <div>
          <div className="login-headline">Break the language barrier in real time</div>
          <div className="login-tagline">German ↔ English, live, during speech</div>
          <div style={{ marginTop: 20 }}>
            <div className="quote-bubble">
              <div className="quote-de">"Ich brauche einen Arzt."</div>
              <div className="quote-arrow">↓ instantly</div>
              <div className="quote-en">"I need a doctor."</div>
            </div>
            <div className="quote-bubble">
              <div className="quote-de">"Where is the nearest pharmacy?"</div>
              <div className="quote-arrow">↓ sofort</div>
              <div className="quote-en">"Wo ist die nächste Apotheke?"</div>
            </div>
          </div>
        </div>
      </div>

      <div className="login-right">
        <h2>{mode === "signin" ? "Sign in" : "Create account"}</h2>
        <p>Access your sessions and conversation history</p>

        {error && <div className="login-error">{error}</div>}

        <form onSubmit={handleSubmit}>
          {mode === "signup" && (
            <div className="field">
              <label>Name</label>
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Your name" />
            </div>
          )}
          <div className="field">
            <label>Email</label>
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" required />
          </div>
          <div className="field">
            <label>Password</label>
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="••••••••" required />
          </div>
          <button className="login-submit" type="submit" disabled={loading}>
            {loading ? "Please wait…" : mode === "signin" ? "Sign in" : "Create account"}
          </button>
        </form>

        <div className="divider">or</div>
        <button className="btn-google" onClick={handleGoogle}>
          <i className="ti ti-brand-google" style={{ fontSize: 16 }} />
          Continue with Google
        </button>

        <div className="signup-link">
          {mode === "signin" ? (
            <>No account? <a onClick={() => setMode("signup")}>Create one free</a></>
          ) : (
            <>Already have an account? <a onClick={() => setMode("signin")}>Sign in</a></>
          )}
        </div>
      </div>
    </div>
  );
}
