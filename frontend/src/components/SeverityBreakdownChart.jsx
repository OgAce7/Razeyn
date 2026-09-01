import React from "react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from "recharts";

const SEVERITY_COLOR = {
  LOW: "var(--sev-low)",
  MEDIUM: "var(--sev-medium)",
  HIGH: "var(--sev-high)",
  CRITICAL: "var(--sev-critical)",
};

/**
 * @param {{ data: {severity: string, count: number}[] }} props
 */
export function SeverityBreakdownChart({ data }) {
  if (!data || data.length === 0) {
    return <div className="empty-state">No incidents yet.</div>;
  }
  return (
    <ResponsiveContainer width="100%" height={160}>
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 20, left: 0, bottom: 4 }}>
        <XAxis type="number" hide />
        <YAxis
          type="category"
          dataKey="severity"
          tick={{ fill: "var(--text-secondary)", fontSize: 12, fontWeight: 600 }}
          axisLine={false}
          tickLine={false}
          width={78}
        />
        <Tooltip
          cursor={{ fill: "rgba(255,255,255,0.03)" }}
          contentStyle={{
            background: "var(--bg-elevated)",
            border: "1px solid var(--border)",
            borderRadius: 10,
            fontSize: 12.5,
          }}
          formatter={(value) => [value, "Incidents"]}
        />
        <Bar dataKey="count" radius={[0, 6, 6, 0]} barSize={22}>
          {data.map((entry) => (
            <Cell key={entry.severity} fill={SEVERITY_COLOR[entry.severity]} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
