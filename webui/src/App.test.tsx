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
        readiness: "unknown_parse_surface",
        price_candidates_count: 2,
        selected_candidate_rank: 1,
        candidate_sources: ["visible_text"],
      }],
      failureRows: [],
      displayRows: [],
      rowsByDate: [],
      quoteSnapshotsByDate: [],
      trust: { fetchQualityTelemetry: {}, parserRecoveryTelemetry: {}, repairPlan: { summary: {}, tasks: [] } },
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
    expect(await screen.findByLabelText("出发日期")).toHaveAttribute("type", "date");
    fireEvent.click(await screen.findByLabelText("环境状态: 未检查"));
    expect(await screen.findByText("环境状态")).toBeInTheDocument();
  });

  it("keeps the first-launch canvas focused on the query card", async () => {
    render(<App />);

    expect(await screen.findByText("开始比价")).toBeInTheDocument();
    expect(screen.queryByText("最低价结论")).not.toBeInTheDocument();
    expect(screen.queryByText("推荐下单方案")).not.toBeInTheDocument();
    expect(screen.queryByText("Top 方案")).not.toBeInTheDocument();
    expect(screen.queryByText("显示原始结果")).not.toBeInTheDocument();
    expect(screen.queryByText("运行日志")).not.toBeInTheDocument();
    expect(screen.queryByText("等待秒数")).not.toBeInTheDocument();
    expect(screen.queryByText("保存汇总")).not.toBeInTheDocument();

    const advancedToggle = await screen.findByRole("button", { name: "高级搜索设置" });
    expect(advancedToggle).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(advancedToggle);
    expect(advancedToggle).toHaveAttribute("aria-expanded", "true");
    expect(await screen.findByText("等待秒数")).toBeInTheDocument();
    expect(await screen.findByText("保存汇总")).toBeInTheDocument();
  });

  it("shows parser details in result evidence", async () => {
    const state = stateWithEvidence();
    window.pywebview = { api: { get_initial_state: async () => state, get_ui_state: async () => state } };

    render(<App />);

    fireEvent.click(await screen.findByText("查看详细结果"));
    expect(await screen.findByText("显示原始结果")).toBeInTheDocument();
    expect(screen.queryByText("成功结果")).not.toBeInTheDocument();
    fireEvent.click(await screen.findByText("显示原始结果"));
    expect(await screen.findByText("Best ¥1300 Cheapest ¥1234")).toBeInTheDocument();
    fireEvent.click(await screen.findByText("详情"));
    expect(await screen.findByText("完整警告")).toBeInTheDocument();
  });

  it("shows scan error reason directly in the status bar", async () => {
    const state = stateWithEvidence();
    state.status = {
      message: "失败",
      busy: false,
      error: "目的地不能为空。",
      progress: { step: 0, total: 0, date: "", regionName: "" },
    };
    state.results.successRows = [];
    window.pywebview = { api: { get_initial_state: async () => state, get_ui_state: async () => state } };

    render(<App />);

    expect(await screen.findByText("失败: 目的地不能为空。")).toBeInTheDocument();
  });

  it("shows failure row error reasons in the raw results table and detail panel", async () => {
    const state = stateWithEvidence();
    state.results.successRows = [];
    state.results.failureRows = [
      {
        date: "2026-05-20",
        route: "PEK-ISB",
        region_name: "巴基斯坦",
        region_code: "PK",
        status: "browser_unavailable",
        error: "browser-unavailable: no launchable browser",
        failure_category: "network",
        failure_action: "复用已打开的页面后重试",
        link: "https://example.test/pk",
      },
    ];
    window.pywebview = { api: { get_initial_state: async () => state, get_ui_state: async () => state } };

    render(<App />);

    fireEvent.click(await screen.findByText("查看详细结果"));
    fireEvent.click(await screen.findByText("显示原始结果"));

    expect(await screen.findByText("错误原因")).toBeInTheDocument();
    expect(await screen.findByText("browser-unavailable: no launchable browser")).toBeInTheDocument();
    fireEvent.click(await screen.findByText("详情"));
    expect(await screen.findByText("失败详情")).toBeInTheDocument();
    expect(await screen.findAllByText("browser_unavailable")).toHaveLength(2);
  });

  it("opens the complete country dropdown for an empty country field", async () => {
    const state = stateWithEvidence();
    state.form.destination = "";
    state.form.destination_country = true;
    state.results.successRows = [];
    let emptyCountryLookupSeen = false;
    window.pywebview = {
      api: {
        get_initial_state: async () => state,
        get_ui_state: async () => state,
        update_query_state: async () => state,
        get_location_suggestions: async (field, query, options) => {
          emptyCountryLookupSeen =
            emptyCountryLookupSeen ||
            (field === "destination" && query === "" && Boolean(options?.destinationCountry));
          return {
            field,
            items: [
              { name: "中国", code: "CN", kind: "country", label: "中国 (CN, 国家)" },
              { name: "巴基斯坦", code: "PK", kind: "country", label: "巴基斯坦 (PK, 国家)" },
            ],
          };
        },
      },
    };

    render(<App />);

    fireEvent.focus(await screen.findByPlaceholderText("例如：东京"));

    expect(await screen.findByText("巴基斯坦 (PK, 国家)")).toBeInTheDocument();
    expect(emptyCountryLookupSeen).toBe(true);
  });
});
