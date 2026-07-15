import { describe, expect, it } from "vitest";
import { summarizeItinerary } from "./itinerary";

describe("summarizeItinerary", () => {
  it("summarizes a one-way itinerary explicitly", () => {
    const summary = summarizeItinerary([
      {
        direction: "outbound",
        departure_time: "08:10",
        arrival_time: "13:25",
        duration_minutes: 315,
        stop_count: 1,
      },
    ]);

    expect(summary.durationLabel).toBe("单程总时长");
    expect(summary.durationText).toBe("5小时15分");
    expect(summary.stopsLabel).toBe("单程转机");
    expect(summary.stopsText).toBe("共 1 次");
    expect(summary.detailLines).toEqual([
      "单程 08:10–13:25 · 5小时15分 · 转机1次",
    ]);
  });

  it("sums complete round-trip duration and stops", () => {
    const summary = summarizeItinerary([
      { direction: "outbound", duration_minutes: 195, stop_count: 0 },
      { direction: "return", duration_minutes: 280, stop_count: 1 },
    ]);

    expect(summary.durationLabel).toBe("往返总时长");
    expect(summary.durationText).toBe("7小时55分");
    expect(summary.stopsLabel).toBe("往返转机");
    expect(summary.stopsText).toBe("共 1 次");
  });

  it("does not present a partial round-trip value as the total", () => {
    const summary = summarizeItinerary([
      { direction: "outbound", duration_minutes: 195, stop_count: 0 },
      { direction: "return", duration_minutes: null, stop_count: null },
    ]);

    expect(summary.hasMetrics).toBe(true);
    expect(summary.durationText).toBe("");
    expect(summary.stopsText).toBe("");
  });
});
