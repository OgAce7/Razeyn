/**
 * Formatting helpers used across the dashboard. Every function here is a
 * pure display transform of a value already returned by the API
 * client/mock data -- none of them compute or estimate a number, they
 * only format one that's already there.
 */

const INR = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});

const INR_PRECISE = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 2,
});

export function formatMoney(value, { precise = false } = {}) {
  if (value === null || value === undefined) return "—";
  return (precise ? INR_PRECISE : INR).format(value);
}

export function formatCompactMoney(value) {
  if (value === null || value === undefined) return "—";
  if (Math.abs(value) >= 100000) {
    return `₹${(value / 100000).toFixed(2)}L`;
  }
  return INR.format(value);
}

export function formatPercent(value, { digits = 1 } = {}) {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function formatNumber(value) {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("en-IN").format(value);
}

export function formatDateTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
}

export function formatDuration(seconds) {
  if (seconds === null || seconds === undefined) return "—";
  const abs = Math.abs(seconds);
  const days = abs / 86400;
  if (days >= 1) return `${days.toFixed(1)}d`;
  const hours = abs / 3600;
  if (hours >= 1) return `${hours.toFixed(1)}h`;
  const mins = abs / 60;
  return `${mins.toFixed(0)}m`;
}

/** Human label for an action enum value, e.g. RETRY_ELIGIBLE_PAYMENTS -> "Retry eligible payments" */
export function humanizeAction(value) {
  if (!value) return "—";
  return value
    .toLowerCase()
    .split("_")
    .map((w) => w[0].toUpperCase() + w.slice(1))
    .join(" ");
}

export function humanizeStatus(value) {
  return humanizeAction(value);
}

export function formatSegment(segment) {
  if (!segment || Object.keys(segment).length === 0) return "All segments";
  return Object.entries(segment)
    .map(([k, v]) => `${humanizeAction(k)}: ${v}`)
    .join(" · ");
}
