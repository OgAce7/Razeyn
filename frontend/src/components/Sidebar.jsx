import React from "react";
import { NavLink } from "react-router-dom";

const NAV_ITEMS = [
  { to: "/", label: "Overview", icon: "◧" },
  { to: "/incidents", label: "Incidents", icon: "◈" },
];

export function Sidebar() {
  return (
    <aside style={styles.sidebar}>
      <div style={styles.brandRow}>
        <div style={styles.logoMark}>RI</div>
        <div>
          <div style={styles.brandName}>Revenue Incident</div>
          <div style={styles.brandSub}>Responder</div>
        </div>
      </div>

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
    color: "#03101f",
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
