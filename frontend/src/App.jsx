import { useEffect, useState } from "react";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";

export default function App() {
  const [status, setStatus] = useState("checking...");

  useEffect(() => {
    fetch(`${API_BASE}/health`)
      .then((res) => res.json())
      .then((data) => setStatus(`${data.status} (${data.env})`))
      .catch(() => setStatus("unreachable"));
  }, []);

  return (
    <div style={{ fontFamily: "sans-serif", padding: "2rem" }}>
      <h1>Razeyn</h1>
      <p>AI Revenue Recovery Agent — hackathon scaffold.</p>
      <p>
        Backend status: <strong>{status}</strong>
      </p>
      <p style={{ color: "#666" }}>
        Dashboard, incident feed, and agent trace views are not built yet.
      </p>
    </div>
  );
}
