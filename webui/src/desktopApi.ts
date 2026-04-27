import type { FormState, SuggestionResponse, UIState } from "./types";

type DesktopApiShape = {
  get_initial_state(): Promise<UIState>;
  get_ui_state(): Promise<UIState>;
  update_query_state(payload: FormState): Promise<UIState>;
  get_location_suggestions(
    field: "origin" | "destination",
    query: string,
    options?: Record<string, unknown>
  ): Promise<SuggestionResponse>;
  start_scan(payload?: Record<string, unknown>): Promise<{ ok: boolean }>;
  cancel_scan(): Promise<{ ok: boolean }>;
  check_environment(): Promise<{ ok: boolean; lines: string[]; issues: string[] }>;
  open_link(url: string): Promise<boolean>;
  open_outputs(): Promise<boolean>;
  export_decision_summary(): Promise<{ markdownPath: string; csvPath: string }>;
  list_history(): Promise<UIState["history"]>;
  apply_history_record(recordId: number | string): Promise<UIState>;
  toggle_favorite_current_query(payload?: Record<string, unknown>): Promise<Record<string, unknown>>;
  save_alert_config(payload: Record<string, unknown>): Promise<Record<string, unknown>>;
  clear_alert_config(payload?: Record<string, unknown>): Promise<Record<string, unknown>>;
  queue_failure_region(payload: Record<string, unknown>): Promise<Record<string, unknown>>;
  run_retry_queue(): Promise<Record<string, unknown>>;
};

declare global {
  interface Window {
    pywebview?: {
      api?: Partial<DesktopApiShape>;
    };
  }
}

const createMockState = (): UIState => ({
  form: {
    origin: "北京",
    destination: "阿拉木图",
    trip_type: "one_way",
    date: "2026-05-20",
    return_date: "",
    regions: "",
    wait: "10",
    date_window: "3",
    exact_airport: false,
    origin_country: false,
    destination_country: false,
    combined_summary: true,
  },
  hints: {
    origin: "",
    destination: "",
    regions: "默认包含 CN,HK,SG,UK；本次实际地区: CN, HK, SG, UK",
    effectiveRegions: ["CN", "HK", "SG", "UK"],
  },
  status: {
    message: "就绪",
    busy: false,
    error: null,
    progress: { step: 0, total: 0, date: "", regionName: "" },
  },
  environment: { lines: [] },
  logs: [{ timestamp: "00:00:00", message: "开发模式：当前未连接 pywebview。" }],
  history: { favorites: [], recent: [], historyDetail: "等待扫描后生成路线复盘。" },
  alerts: { config: null, summary: "未设置提醒。", pendingRetryRegions: [] },
  results: {
    cheapestConclusion: {
      headline: "等待比价开始",
      price: "这里会出现最低价结论",
      supporting: "完成扫描后自动更新",
      meta: "",
      insight: "开发模式下只展示静态占位。",
      button_text: "等待结果",
      link: null,
    },
    recommendationConclusion: {
      headline: "等待推荐方案",
      price: "暂无可比较价格",
      supporting: "完成扫描后生成推荐下单方案",
      meta: "",
      insight: "开发模式下只展示静态占位。",
      button_text: "等待结果",
      link: null,
    },
    topRecommendations: [],
    calendar: { kind: "empty", cells: [] },
    compareRows: [],
    successRows: [],
    failureRows: [],
    displayRows: [],
    rowsByDate: [],
    quoteSnapshotsByDate: [],
  },
  outputs: { currentOutput: null, reportsDir: "" },
});

class MockDesktopApi implements DesktopApiShape {
  private state = createMockState();

  async get_initial_state(): Promise<UIState> {
    return this.state;
  }

  async get_ui_state(): Promise<UIState> {
    return this.state;
  }

  async update_query_state(payload: FormState): Promise<UIState> {
    this.state = { ...this.state, form: { ...payload } };
    return this.state;
  }

  async get_location_suggestions(
    field: "origin" | "destination",
    query: string,
    options?: Record<string, unknown>
  ): Promise<SuggestionResponse> {
    const useCountry =
      field === "origin"
        ? Boolean(options?.originCountry)
        : Boolean(options?.destinationCountry);
    if (!query.trim()) {
      return { field, items: [] };
    }
    if (useCountry) {
      return {
        field,
        items: [
          { name: "菲律宾", code: "PH", kind: "country", label: "菲律宾 (国家)" },
          { name: "芬兰", code: "FI", kind: "country", label: "芬兰 (国家)" },
        ].filter((i) => i.name.includes(query)),
      };
    }
    return {
      field,
      items: [
        { name: query, code: "MOCK", kind: "metro", label: `${query} (MOCK, 城市)` },
      ],
    };
  }

  async start_scan(): Promise<{ ok: boolean }> {
    return { ok: true };
  }

  async cancel_scan(): Promise<{ ok: boolean }> {
    return { ok: true };
  }

  async check_environment(): Promise<{ ok: boolean; lines: string[]; issues: string[] }> {
    return { ok: false, lines: ["开发模式下未连接 Python bridge"], issues: [] };
  }

  async open_link(): Promise<boolean> {
    return true;
  }

  async open_outputs(): Promise<boolean> {
    return true;
  }

  async export_decision_summary(): Promise<{ markdownPath: string; csvPath: string }> {
    return { markdownPath: "mock.md", csvPath: "mock.csv" };
  }

  async list_history(): Promise<UIState["history"]> {
    return this.state.history;
  }

  async apply_history_record(): Promise<UIState> {
    return this.state;
  }

  async toggle_favorite_current_query(): Promise<Record<string, unknown>> {
    return { isFavorite: false };
  }

  async save_alert_config(): Promise<Record<string, unknown>> {
    return {};
  }

  async clear_alert_config(): Promise<Record<string, unknown>> {
    return {};
  }

  async queue_failure_region(): Promise<Record<string, unknown>> {
    return {};
  }

  async run_retry_queue(): Promise<Record<string, unknown>> {
    return {};
  }
}

const mockApi = new MockDesktopApi();

function getRealApi(): Partial<DesktopApiShape> | undefined {
  return window.pywebview?.api;
}

function hasApiMethod(
  api: Partial<DesktopApiShape> | undefined,
  methodName: keyof DesktopApiShape
): api is DesktopApiShape {
  return typeof api?.[methodName] === "function";
}

async function waitForPywebviewApi(methodName: keyof DesktopApiShape, timeoutMs = 1500): Promise<DesktopApiShape | undefined> {
  const existing = getRealApi();
  if (hasApiMethod(existing, methodName)) {
    return existing;
  }

  if (!window.pywebview) {
    return undefined;
  }

  return new Promise((resolve) => {
    let settled = false;

    const finish = () => {
      if (settled) return;
      settled = true;
      window.removeEventListener("pywebviewready", handleReady as EventListener);
      window.clearTimeout(timer);
      const nextApi = getRealApi();
      resolve(hasApiMethod(nextApi, methodName) ? nextApi : undefined);
    };

    const handleReady = () => {
      finish();
    };

    const timer = window.setTimeout(finish, timeoutMs);
    window.addEventListener("pywebviewready", handleReady as EventListener, { once: true });

    const recheckApi = getRealApi();
    if (hasApiMethod(recheckApi, methodName)) {
      finish();
    }
  });
}

export const desktopApi: DesktopApiShape = new Proxy({} as DesktopApiShape, {
  get(_, prop: string | symbol) {
    if (typeof prop !== "string") return undefined;
    const methodName = prop as keyof DesktopApiShape;
    const realApi = getRealApi();
    const target = hasApiMethod(realApi, methodName) ? realApi : mockApi;
    const value = (target as any)[prop];
    if (typeof value === "function") {
      return async function (this: unknown, ...args: unknown[]) {
        const resolvedApi = await waitForPywebviewApi(methodName);
        const t = resolvedApi && hasApiMethod(resolvedApi, methodName) ? resolvedApi : mockApi;
        return (t as any)[prop].apply(t, args);
      };
    }
    return value;
  },
});
