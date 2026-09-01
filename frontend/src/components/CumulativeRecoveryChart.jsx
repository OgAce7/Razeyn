import React from "react";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from "recharts";
import { formatCompactMoney, formatMoney, formatDate } from "../lib/format.js";

/**
 * @param {{ data: {id: string, timestamp: string, cumulativeRecovered: number}[] }} props
 */
export function CumulativeRecoveryChart({ data }) {
  if (!data || data.length === 0) {
    return <div className="empty-state">No recovery activity yet.</div>;
  }
  return (
    <ResponsiveContainer width="100%" height={220}>
      <AreaChart data={data} margin={{ top: 8, right: 12, left: -12, bottom: 4 }}>
        <defs>
          <linearGradient id="recoveryFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--positive)" stopOpacity={0.35} />
            <stop offset="100%" stopColor="var(--positive)" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border-soft)" vertical={false} />
        <XAxis
          dataKey="timestamp"
          tickFormatter={(v) => formatDate(v)}
          tick={{ fill: "var(--text-tertiary)", fontSize: 11 }}
          axisLine={{ stroke: "var(--border)" }}
          tickLine={false}
          minTickGap={24}
        />
        <YAxis
          tick={{ fill: "var(--text-tertiary)", fontSize: 11 }}
          axisLine={false}
          tickLine={false}
          tickFormatter={(v) => formatCompactMoney(v)}
          width={64}
        />
        <Tooltip
          contentStyle={{
            background: "var(--bg-elevated)",
            border: "1px solid var(--border)",
            borderRadius: 10,
            fontSize: 12.5,
          }}
          labelFormatter={(v) => formatDate(v)}
          labelStyle={{ color: "var(--text-primary)", fontWeight: 600, marginBottom: 4 }}
          formatter={(value) => [formatMoney(value, { precise: true }), "Cumulative recovered"]}
        />
        <Area
          type="monotone"
          dataKey="cumulativeRecovered"
          stroke="var(--positive)"
          strokeWidth={2}
          fill="url(#recoveryFill)"
          dot={{ r: 3, fill: "var(--positive)", strokeWidth: 0 }}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
