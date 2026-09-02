import React from "react";
import { NavLink } from "react-router-dom";
import { DatasetSwitcher } from "./DatasetSwitcher.jsx";
import { useTheme } from "../lib/ThemeContext.jsx";

const NAV_ITEMS = [
  { to: "/", label: "Overview", icon: "◧" },
  { to: "/incidents", label: "Incidents", icon: "◈" },
  { to: "/upload", label: "Upload dataset", icon: "⇪" },
];

export function Sidebar() {
  const { theme, toggleTheme } = useTheme();

  return (
    <aside style={styles.sidebar}>
      <div style={styles.brandRow}>
        <div style={styles.logoMark}>RI</div>
        <div style={styles.brandTextBlock}>
          <div style={styles.brandName}>Revenue Incident</div>
          <div style={styles.brandSub}>Responder</div>
        </div>
        <button
          type="button"
          onClick={toggleTheme}
          className="theme-toggle"
          style={styles.themeToggle}
          title={theme === "light" ? "Switch to dark mode" : "Switch to light mode"}
          aria-label={theme === "light" ? "Switch to dark mode" : "Switch to light mode"}
        >
          {theme === "light" ? "☾" : "☼"}
        </button>
      </div>

      <DatasetSwitcher />

      <nav style={styles.nav}>
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === "/"}
            style={({ isActive }) => ({
              ...styles.navItem,
              ...(isActive ? styles.navItemActive : {}),
            })}
          >
            <span style={styles.navIcon}>{item.icon}</span>
            {item.label}
          </NavLink>
        ))}
      </nav>

      <div style={styles.footer}>
        <div style={styles.footerLine}>Story flow</div>
        <ol style={styles.storyList}>
          {["Revenue at risk", "Incident detected", "Evidence", "AI diagnosis", "Recovery decision", "Action", "Money recovered"].map(
            (step, i) => (
              <li key={step} style={styles.storyItem}>
                <span style={styles.storyNum}>{i + 1}</span>
                {step}
              </li>
            )
          )}
        </ol>
      </div>
    </aside>
  );
}

const styles = {
  sidebar: {
    width: 240,
    flexShrink: 0,
    borderRight: "1px solid var(--border-soft)",
    background: "var(--bg-elevated)",
    padding: "22px 18px",
    display: "flex",
    flexDirection: "column",
    position: "sticky",
    top: 0,
    height: "100vh",
  },
  brandRow: {
    display: "flex",
    alignItems: "center",
    gap: 10,
    marginBottom: 28,
    padding: "0 6px",
  },
  brandTextBlock: {
    flex: 1,
    minWidth: 0,
  },
  themeToggle: {
    width: 28,
    height: 28,
    flexShrink: 0,
    borderRadius: 8,
    border: "1px solid var(--border)",
    background: "var(--bg-card)",
    color: "var(--text-secondary)",
    fontSize: 13,
    cursor: "pointer",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    transition: "background 0.15s ease, color 0.15s ease",
  },
  logoMark: {
    width: 34,
    height: 34,
    borderRadius: 9,
    background: "linear-gradient(135deg, var(--accent), var(--accent-strong))",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    fontWeight: 700,
    fontSize: 13,
    color: "var(--on-accent)",
    flexShrink: 0,
  },
  brandName: {
    fontSize: 13.5,
    fontWeight: 700,
    lineHeight: 1.2,
    color: "var(--text-primary)",
  },
  brandSub: {
    fontSize: 11.5,
    color: "var(--text-tertiary)",
    letterSpacing: "0.02em",
  },
  nav: {
    display: "flex",
    flexDirection: "column",
    gap: 4,
  },
  navItem: {
    display: "flex",
    alignItems: "center",
    gap: 10,
    padding: "9px 12px",
    borderRadius: 10,
    fontSize: 13.5,
    fontWeight: 500,
    color: "var(--text-secondary)",
  },
  navItemActive: {
    background: "var(--accent-soft)",
    color: "var(--accent-strong)",
    fontWeight: 600,
  },
  navIcon: {
    fontSize: 14,
    width: 16,
    textAlign: "center",
  },
  footer: {
    marginTop: "auto",
    paddingTop: 20,
    borderTop: "1px solid var(--border-soft)",
  },
  footerLine: {
    fontSize: 10.5,
    fontWeight: 600,
    letterSpacing: "0.08em",
    textTransform: "uppercase",
    color: "var(--text-tertiary)",
    marginBottom: 10,
  },
  storyList: {
    margin: 0,
    padding: 0,
    listStyle: "none",
    display: "flex",
    flexDirection: "column",
    gap: 7,
    fontSize: 12,
    color: "var(--text-secondary)",
    counterReset: "story",
  },
  storyItem: {
    display: "flex",
    alignItems: "center",
    gap: 8,
  },
  storyNum: {
    width: 16,
    height: 16,
    borderRadius: 5,
    background: "var(--bg-card)",
    border: "1px solid var(--border)",
    fontSize: 9.5,
    fontWeight: 700,
    color: "var(--text-tertiary)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    flexShrink: 0,
  },
};
