export function formatMoney(value: unknown): string {
  return typeof value === "number" ? `¥${value.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}` : "-";
}

const priceSourceLabels: Record<string, string> = {
  cheapest_block: "Cheapest",
  best_block: "Best",
  first_price_fallback: "弱匹配",
  recovered_best: "Recovered",
  manual_confirmed: "Confirmed",
  unpriced: "No price",
};

export function confidenceLabel(value: unknown): string {
  if (typeof value !== "number") return "未知";
  if (value >= 0.85) return "高";
  if (value >= 0.6) return "中";
  if (value >= 0.3) return "低";
  return "极低";
}

export function confidenceClass(value: unknown): string {
  if (typeof value !== "number") return "unknown";
  if (value >= 0.85) return "high";
  if (value >= 0.6) return "medium";
  return "low";
}

export function priceSourceLabel(value: unknown): string {
  const key = String(value ?? "").trim();
  if (!key || key === "unknown") return "未知";
  return priceSourceLabels[key] ?? key;
}

export function warningsSummary(value: unknown): string {
  if (!Array.isArray(value)) return "-";
  const cleaned = value.map((item) => String(item).trim()).filter(Boolean);
  if (!cleaned.length) return "-";
  if (cleaned.length === 1) return cleaned[0];
  return `${cleaned.length} 项`;
}

export function numberValue(value: unknown): number {
  return typeof value === "number" ? value : 0;
}

export function listSummary(value: unknown): string {
  if (!Array.isArray(value)) return "-";
  const cleaned = value.map((item) => String(item).trim()).filter(Boolean);
  return cleaned.length ? cleaned.join(", ") : "-";
}
