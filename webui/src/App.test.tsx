import { afterEach, describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import App from "./App";
import type { UIState } from "./types";

function stateWithEvidence(): UIState {
  return {
    form: {
      origin: "北京",
      destination: "阿拉木图",
      trip_type: "one_way",
      date: "2026-05-20",
      return_date: "",
      regions: "",
      wait: "10",
      date_window: "0",
      exact_airport: false,
      origin_country: false,
      destination_country: false,
      combined_summary: true,
    },
    hints: { origin: "", destination: "", regions: "", effectiveRegions: ["CN"] },
    status: { message: "就绪", busy: false, error: null, progress: { step: 0, total: 0, date: "", regionName: "" } },
    environment: { lines: [] },
    logs: [],
    history: { favorites: [], recent: [], historyDetail: "" },
    alerts: { config: null, summary: "", pendingRetryRegions: [] },
    results: {
      cheapestConclusion: { headline: "最低价", price: "¥1,234", supporting: "", meta: "", insight: "", button_text: "打开", link: null },
      recommendationConclusion: { headline: "推荐", price: "¥1,234", supporting: "", meta: "", insight: "", button_text: "打开", link: null },
      topRecommendations: [],
      calendar: { kind: "empty", cells: [] },
      compareRows: [],
      successRows: [{
        date: "2026-05-20",
        route: "PEK-ALA",
        region_name: "中国",
        region_code: "CN",
        link: "https://example.test",
        cheapest_cny_price: 1234,
        best_cny_price: 1300,
        confidence: 0.42,
        price_source: "first_price_fallback",
        parser_warnings: ["首个价格 fallback"],
        evidence_text: "Best ¥1300 Cheapest ¥1234",
        fallback_attempts: [{ transport: "opencli", status: "opencli_error", error: "timeout" }],
        readiness: "unknown_parse_surface",
        price_candidates_count: 2,
        selected_candidate_rank: 1,
        candidate_sources: ["visible_text"],
      }],
      failureRows: [],
      displayRows: [],
      rowsByDate: [],
      quoteSnapshotsByDate: [],
      trust: { fetchQualityTelemetry: {}, parserRecoveryTelemetry: {}, snapshotSummary: {}, repairPlan: { summary: {}, tasks: [] } },
    },
    outputs: { currentOutput: null, reportsDir: "" },
  };
}

afterEach(() => {
  delete window.pywebview;
});

describe("App", () => {
  it("renders the compact desktop shell", async () => {
    render(<App />);
    expect(await screen.findByText("Skyscanner")).toBeInTheDocument();
    expect(await screen.findByText("开始比价")).toBeInTheDocument();
    expect(await screen.findByPlaceholderText("例如：北京")).toBeInTheDocument();
    expect(await screen.findByPlaceholderText("例如：东京")).toBeInTheDocument();
  });

  it("shows full fallback details in result evidence", async () => {
    const state = stateWithEvidence();
    window.pywebview = { api: { get_initial_state: async () => state, get_ui_state: async () => state } };

    render(<App />);

    fireEvent.click(await screen.findByText("查看详细结果"));
    expect(await screen.findByText(/opencli · opencli_error · timeout/)).toBeInTheDocument();
    fireEvent.click(await screen.findByText("详情"));
    expect(await screen.findByText("完整警告")).toBeInTheDocument();
  });
});
