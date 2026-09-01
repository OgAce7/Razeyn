import React from "react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
  Legend,
} from "recharts";
import { formatCompactMoney, formatMoney } from "../lib/format.js";

/**
 * @param {{ data: {id: string, label: string, atRisk: number, recovered: number}[] }} props
 */
export function RevenueByIncidentChart({ data }) {
  if (!data || data.length === 0) {
    return <div className="empty-state">No incidents to chart yet.</div>;
  }
  return (
    <ResponsiveContainer width="100%" height={280}>
      <BarChart data={data} margin={{ top: 4, right: 8, left: -12, bottom: 4 }} barGap={4}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border-soft)" vertical={false} />
        <XAxis
          dataKey="label"
          tick={{ fill: "var(--text-tertiary)", fontSize: 11 }}
          axisLine={{ stroke: "var(--border)" }}
          tickLine={false}
          interval={0}
          angle={-18}
          textAnchor="end"
          height={54}
        />
        <YAxis
          tick={{ fill: "var(--text-tertiary)", fontSize: 11 }}
          axisLine={false}
          tickLine={false}
          tickFormatter={(v) => formatCompactMoney(v)}
          width={64}
        />
        <Tooltip
          cursor={{ fill: "rgba(255,255,255,0.03)" }}
          contentStyle={{
            background: "var(--bg-elevated)",
            border: "1px solid var(--border)",
            borderRadius: 10,
            fontSize: 12.5,
          }}
          labelStyle={{ color: "var(--text-primary)", fontWeight: 600, marginBottom: 4 }}
          formatter={(value, name) => [formatMoney(value, { precise: true }), name]}
        />
        <Legend
          wrapperStyle={{ fontSize: 12, color: "var(--text-secondary)" }}
          iconType="circle"
          iconSize={8}
        />
        <Bar dataKey="atRisk" name="Revenue at risk" fill="var(--accent-soft)" stroke="var(--accent)" strokeWidth={1} radius={[4, 4, 0, 0]} />
        <Bar dataKey="recovered" name="Revenue recovered" fill="var(--positive)" radius={[4, 4, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}
