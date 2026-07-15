import type { ItineraryLeg } from "../types";

export type ItinerarySummaryData = {
  legs: ItineraryLeg[];
  durationLabel: string;
  durationText: string;
  stopsLabel: string;
  stopsText: string;
  detailLines: string[];
  hasMetrics: boolean;
};

function validInteger(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0
    ? value
    : null;
}

export function normalizeItineraryLegs(value: unknown): ItineraryLeg[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((leg): leg is ItineraryLeg => Boolean(leg) && typeof leg === "object")
    .slice(0, 2);
}

export function formatDuration(minutes: unknown): string {
  const normalized = validInteger(minutes);
  if (normalized === null || normalized <= 0) return "";
  const hours = Math.floor(normalized / 60);
  const remainder = normalized % 60;
  if (hours && remainder) return `${hours}小时${remainder}分`;
  if (hours) return `${hours}小时`;
  return `${remainder}分钟`;
}

export function summarizeItinerary(value: unknown): ItinerarySummaryData {
  const legs = normalizeItineraryLegs(value);
  const durations = legs.map((leg) => validInteger(leg.duration_minutes));
  const stops = legs.map((leg) => validInteger(leg.stop_count));
  const hasAnyDuration = durations.some((duration) => duration !== null && duration > 0);
  const hasAnyStops = stops.some((stop) => stop !== null);
  const hasCompleteDuration = durations.length > 0
    && durations.every((duration) => duration !== null && duration > 0);
  const hasCompleteStops = stops.length > 0 && stops.every((stop) => stop !== null);
  const totalDuration = durations.reduce<number>(
    (total, duration) => total + (duration ?? 0),
    0,
  );
  const totalStops = stops.reduce<number>((total, stop) => total + (stop ?? 0), 0);
  const roundTrip = legs.some((leg) => leg.direction === "return");

  const detailLines = legs.flatMap((leg, index) => {
    const details: string[] = [];
    if (leg.departure_time && leg.arrival_time) {
      details.push(`${leg.departure_time}–${leg.arrival_time}`);
    }
    const duration = formatDuration(leg.duration_minutes);
    if (duration) details.push(duration);
    const stopCount = validInteger(leg.stop_count);
    if (stopCount !== null) {
      details.push(stopCount === 0 ? "直飞" : `转机${stopCount}次`);
    }
    if (!details.length) return [];
    const direction = roundTrip
      ? leg.direction === "return" || index === 1
        ? "返程"
        : "去程"
      : "单程";
    return [`${direction} ${details.join(" · ")}`];
  });

  return {
    legs,
    durationLabel: roundTrip ? "往返总时长" : "单程总时长",
    durationText: hasCompleteDuration ? formatDuration(totalDuration) : "",
    stopsLabel: roundTrip ? "往返转机" : "单程转机",
    stopsText: hasCompleteStops
      ? totalStops === 0
        ? roundTrip
          ? "均直飞"
          : "直飞"
        : `共 ${totalStops} 次`
      : "",
    detailLines,
    hasMetrics: hasAnyDuration || hasAnyStops,
  };
}
