import React from "react";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { Sidebar } from "./components/Sidebar.jsx";
import OverviewPage from "./pages/OverviewPage.jsx";
import IncidentsPage from "./pages/IncidentsPage.jsx";
import IncidentDetailPage from "./pages/IncidentDetailPage.jsx";

export default function App() {
  return (
    <BrowserRouter>
      <div className="app-shell">
        <Sidebar />
        <main className="app-main">
          <Routes>
            <Route path="/" element={<OverviewPage />} />
            <Route path="/incidents" element={<IncidentsPage />} />
            <Route path="/incidents/:incidentId" element={<IncidentDetailPage />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}

function NotFound() {
  return (
    <div className="empty-state">
      <h2>Page not found</h2>
      <p>That route doesn't exist in Revenue Incident Responder.</p>
    </div>
  );
}
