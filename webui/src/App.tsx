import {
  startTransition,
  useDeferredValue,
  useEffect,
  useMemo,
  useState,
} from "react";
import { desktopApi } from "./desktopApi";
import type { FormState, HistoryRecord, ResultRow, UIState } from "./types";

type SuggestionMap = {
  origin: Array<{ name: string; code: string; kind: string; label: string }>;
  destination: Array<{ name: string; code: string; kind: string; label: string }>;
};

type AlertDraft = {
  targetPrice: string;
  dropAmount: string;
  autoRefreshMinutes: string;
  notificationsEnabled: boolean;
  notifyOnRecovery: boolean;
  notifyOnNewLow: boolean;
};

const defaultAlertDraft: AlertDraft = {
  targetPrice: "",
  dropAmount: "",
  autoRefreshMinutes: "",
  notificationsEnabled: true,
  notifyOnRecovery: true,
  notifyOnNewLow: true,
};

const emptySuggestions: SuggestionMap = {
  origin: [],
  destination: [],
};

function formatMoney(value: unknown): string {
  return typeof value === "number" ? `¥${value.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}` : "-";
}

function SummaryCard({
  eyebrow,
  headline,
  price,
  supporting,
  meta,
  insight,
  buttonText,
  action,
  accent,
}: {
  eyebrow: string;
  headline?: string;
  price?: string;
  supporting?: string;
  meta?: string;
  insight?: string;
  buttonText?: string;
  action?: () => void;
  accent: "gold" | "stone";
}) {
  return (
    <section className={`summary-card summary-card-${accent}`}>
      <p className="eyebrow">{eyebrow}</p>
      <h2>{headline ?? "等待结果"}</h2>
      <div className="summary-price">{price ?? "-"}</div>
      <p className="summary-supporting">{supporting ?? ""}</p>
      <p className="summary-meta">{meta ?? ""}</p>
      <p className="summary-insight">{insight ?? ""}</p>
      <button className="primary-button subtle" onClick={action} type="button">
        {buttonText ?? "等待结果"}
      </button>
    </section>
  );
}

function SectionCard({
  title,
  subtitle,
  actions,
  children,
  className,
}: {
  title: string;
  subtitle?: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={`section-card ${className ?? ""}`.trim()}>
      <div className="section-card-header">
        <div>
          <h3>{title}</h3>
          {subtitle ? <p>{subtitle}</p> : null}
        </div>
        {actions ? <div className="section-actions">{actions}</div> : null}
      </div>
      {children}
    </section>
  );
}

function ToolbarButton({
  active,
  label,
  onClick,
}: {
  active?: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <button className={`toolbar-button ${active ? "active" : ""}`} onClick={onClick} type="button">
      {label}
    </button>
  );
}

function EmptyState({ text }: { text: string }) {
  return <div className="empty-state">{text}</div>;
}

function BootScreen({
  title,
  detail,
  error,
}: {
  title: string;
  detail: string;
  error?: boolean;
}) {
  return (
    <div className={`loading-screen ${error ? "loading-screen-error" : ""}`}>
      <div className="loading-panel">
        <div className="loading-orbit" aria-hidden="true">
          <span />
          <span />
          <span />
        </div>
        <p className="loading-kicker">{error ? "Startup Error" : "Desktop Loading"}</p>
        <h1>{title}</h1>
        <p>{detail}</p>
      </div>
    </div>
  );
}

function DataTable({
  columns,
  rows,
  onOpenLink,
  highlightFailure,
  onQueueRetry,
}: {
  columns: Array<{ key: string; label: string; align?: "right" | "left" }>;
  rows: ResultRow[];
  onOpenLink: (url: string) => void;
  highlightFailure?: boolean;
  onQueueRetry?: (row: ResultRow) => void;
}) {
  if (!rows.length) {
    return <EmptyState text={highlightFailure ? "当前没有失败市场。" : "当前没有可展示结果。"} />;
  }

  return (
    <div className="table-shell">
      <table>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key} className={column.align === "right" ? "align-right" : ""}>
                {column.label}
              </th>
            ))}
            <th>动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => {
            const rowClassName = highlightFailure
              ? row.isReuseReady
                ? "failure-row reuse"
                : "failure-row"
              : row.isCheapestHighlight
                ? "price-row cheapest"
                : row.isChangedHighlight
                  ? "price-row changed"
                  : "price-row";
            return (
              <tr key={`${String(row.date)}-${String(row.route)}-${String(row.region_code)}-${index}`} className={rowClassName}>
                {columns.map((column) => (
                  <td key={column.key} className={column.align === "right" ? "align-right" : ""}>
                    {column.key.includes("price")
                      ? formatMoney(row[column.key])
                      : String(row[column.key] ?? "-")}
                  </td>
                ))}
                <td>
                  <div className="row-actions">
                    {typeof row.link === "string" && row.link.startsWith("http") ? (
                      <button className="ghost-button" onClick={() => onOpenLink(String(row.link))} type="button">
                        打开
                      </button>
                    ) : null}
                    {highlightFailure && onQueueRetry ? (
                      <button className="ghost-button" onClick={() => onQueueRetry(row)} type="button">
                        加入补扫
                      </button>
                    ) : null}
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function HistoryList({
  title,
  records,
  onApply,
}: {
  title: string;
  records: HistoryRecord[];
  onApply: (record: HistoryRecord) => void;
}) {
  return (
    <div className="history-group">
      <div className="history-group-head">
        <span>{title}</span>
        <span>{records.length}</span>
      </div>
      {records.length ? (
        <ul className="history-list">
          {records.map((record) => (
            <li key={record.queryKey}>
              <button type="button" onClick={() => onApply(record)}>
                <span>{record.title}</span>
                <small>{record.createdAt}</small>
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <EmptyState text="暂无记录。" />
      )}
    </div>
  );
}

function App() {
  const [uiState, setUiState] = useState<UIState | null>(null);
  const [form, setForm] = useState<FormState | null>(null);
  const [bootstrapError, setBootstrapError] = useState("");
  const [alertDraft, setAlertDraft] = useState<AlertDraft>(defaultAlertDraft);
  const [insightTab, setInsightTab] = useState<"calendar" | "compare" | "history" | "table">("calendar");
  const [showSuccess, setShowSuccess] = useState(true);
  const [showFailure, setShowFailure] = useState(true);
  const [showChangedOnly, setShowChangedOnly] = useState(false);
  const [showLowestOnly, setShowLowestOnly] = useState(false);
  const [sourceFilter, setSourceFilter] = useState<"all" | "live" | "bookable">("all");
  const [selectedTripLabel, setSelectedTripLabel] = useState("");
  const [actionMessage, setActionMessage] = useState("");
  const [activeField, setActiveField] = useState<"origin" | "destination" | null>(null);
  const [suggestions, setSuggestions] = useState<SuggestionMap>(emptySuggestions);
  const [isPending, setIsPending] = useState(false);

  const originDeferred = useDeferredValue(form?.origin ?? "");
  const destinationDeferred = useDeferredValue(form?.destination ?? "");

  useEffect(() => {
    let cancelled = false;
    desktopApi
      .get_initial_state()
      .then((state) => {
        if (cancelled) {
          return;
        }
        setUiState(state);
        setForm(state.form);
        setAlertDraft({
          targetPrice: state.alerts.config?.target_price ? String(state.alerts.config.target_price) : "",
          dropAmount: state.alerts.config?.drop_amount ? String(state.alerts.config.drop_amount) : "",
          autoRefreshMinutes: state.alerts.config?.auto_refresh_minutes
            ? String(state.alerts.config.auto_refresh_minutes)
            : "",
          notificationsEnabled: state.alerts.config?.notifications_enabled ?? true,
          notifyOnRecovery: state.alerts.config?.notify_on_recovery ?? true,
          notifyOnNewLow: state.alerts.config?.notify_on_new_low ?? true,
        });
      })
      .catch((error: unknown) => {
        if (cancelled) {
          return;
        }
        setBootstrapError(error instanceof Error ? error.message : "桌面桥接初始化失败。");
      });

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!uiState) {
      return;
    }
    const timer = window.setInterval(() => {
      desktopApi.get_ui_state().then((nextState) => {
        startTransition(() => {
          setUiState(nextState);
        });
      });
    }, 1000);
    return () => {
      window.clearInterval(timer);
    };
  }, [uiState]);

  useEffect(() => {
    if (!form) {
      return;
    }
    const timer = window.setTimeout(() => {
      desktopApi.update_query_state(form).catch(() => undefined);
    }, 400);
    return () => {
      window.clearTimeout(timer);
    };
  }, [form]);

  useEffect(() => {
    if (!form || !originDeferred.trim()) {
      setSuggestions((current) => ({ ...current, origin: [] }));
      return;
    }
    desktopApi
      .get_location_suggestions("origin", originDeferred, {
        exactAirport: form.exact_airport,
        originCountry: form.origin_country,
        destinationCountry: form.destination_country,
        preferMetro: !form.exact_airport,
      })
      .then((response) => {
        setSuggestions((current) => ({ ...current, origin: response.items }));
      })
      .catch(() => undefined);
  }, [form, originDeferred]);

  useEffect(() => {
    if (!form || !destinationDeferred.trim()) {
      setSuggestions((current) => ({ ...current, destination: [] }));
      return;
    }
    desktopApi
      .get_location_suggestions("destination", destinationDeferred, {
        exactAirport: form.exact_airport,
        originCountry: form.origin_country,
        destinationCountry: form.destination_country,
        preferMetro: false,
      })
      .then((response) => {
        setSuggestions((current) => ({ ...current, destination: response.items }));
      })
      .catch(() => undefined);
  }, [destinationDeferred, form]);

  const filteredResults = useMemo(() => {
    if (!uiState) {
      return { successRows: [] as ResultRow[], failureRows: [] as ResultRow[] };
    }
    let successRows = [...uiState.results.successRows];
    let failureRows = [...uiState.results.failureRows];
    if (selectedTripLabel) {
      successRows = successRows.filter((row) => String(row.date ?? "") === selectedTripLabel);
      failureRows = failureRows.filter((row) => String(row.date ?? "") === selectedTripLabel);
    }
    if (showChangedOnly) {
      successRows = successRows.filter((row) => !["-", "持平", ""].includes(String(row.delta_label ?? "")));
      failureRows = failureRows.filter((row) => !["-", "持平", ""].includes(String(row.delta_label ?? "")));
    }
    if (showLowestOnly) {
      successRows = successRows.filter((row) => Boolean(row.isCheapestHighlight));
      failureRows = [];
    }
    if (sourceFilter === "live") {
      successRows = successRows.filter((row) => ["live", "browser_fallback", "cdp_reuse"].includes(String(row.source_kind ?? "")));
      failureRows = failureRows.filter((row) => ["live", "browser_fallback", "cdp_reuse"].includes(String(row.source_kind ?? "")));
    }
    if (sourceFilter === "bookable") {
      successRows = successRows.filter((row) => String(row.link ?? "").startsWith("http"));
      failureRows = [];
    }
    if (!showSuccess) {
      successRows = [];
    }
    if (!showFailure) {
      failureRows = [];
    }
    return { successRows, failureRows };
  }, [selectedTripLabel, showChangedOnly, showFailure, showLowestOnly, showSuccess, sourceFilter, uiState]);

  if (bootstrapError) {
    return (
      <BootScreen
        title="桌面桥接初始化失败"
        detail={bootstrapError}
        error
      />
    );
  }

  if (!uiState || !form) {
    return (
      <BootScreen
        title="正在连接桌面服务"
        detail="界面骨架、历史状态和本地 Python bridge 正在同步。首次启动会稍慢一些。"
      />
    );
  }

  const applyFormPatch = (patch: Partial<FormState>) => {
    setForm((current) => (current ? { ...current, ...patch } : current));
  };

  const resetActionMessage = () => window.setTimeout(() => setActionMessage(""), 2400);

  const handleStartScan = async (overrides?: Record<string, unknown>) => {
    setIsPending(true);
    try {
      await desktopApi.start_scan({ form, ...overrides });
      setActionMessage("已开始扫描。");
    } catch (error) {
      setActionMessage(error instanceof Error ? error.message : "启动扫描失败。");
    } finally {
      setIsPending(false);
      resetActionMessage();
    }
  };

  const handleEnvironmentCheck = async () => {
    const result = await desktopApi.check_environment();
    setActionMessage(result.ok ? "环境已检查。" : result.issues[0] ?? "环境检查已完成。");
    resetActionMessage();
  };

  const handleApplyHistory = async (record: HistoryRecord) => {
    const nextState = await desktopApi.apply_history_record(record.id ?? record.queryKey);
    setUiState(nextState);
    setForm(nextState.form);
  };

  const handleSaveAlerts = async () => {
    try {
      await desktopApi.save_alert_config({
        form,
        ...alertDraft,
      });
      const nextState = await desktopApi.get_ui_state();
      setUiState(nextState);
      setActionMessage("提醒设置已保存。");
    } catch (error) {
      setActionMessage(error instanceof Error ? error.message : "提醒保存失败。");
    }
    resetActionMessage();
  };

  const handleQueueFailure = async (row: ResultRow) => {
    await desktopApi.queue_failure_region({
      date: row.date,
      route: row.route,
      regionCode: row.region_code,
      regionName: row.region_name,
    });
    const nextState = await desktopApi.get_ui_state();
    setUiState(nextState);
  };

  const cheapestCard = uiState.results.cheapestConclusion;
  const recommendationCard = uiState.results.recommendationConclusion;
  const successCount = uiState.results.successRows.length;
  const failureCount = uiState.results.failureRows.length;
  const marketCount = uiState.hints.effectiveRegions.length;

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="hero-block">
          <p className="hero-kicker">Skyscanner Multi-Market Desk</p>
          <h1>多市场比价与复扫台</h1>
          <p className="hero-copy">
            保留现有 Python 抓取内核，把查询、最低价决策、历史复盘和自动复扫整合进一个更克制的桌面工作台。
          </p>
          <div className="hero-metrics">
            <div className="hero-metric">
              <span>本次市场</span>
              <strong>{marketCount}</strong>
            </div>
            <div className="hero-metric">
              <span>成功结果</span>
              <strong>{successCount}</strong>
            </div>
            <div className="hero-metric">
              <span>失败市场</span>
              <strong>{failureCount}</strong>
            </div>
          </div>
        </div>
        <div className="header-actions">
          <button className="ghost-button" onClick={handleEnvironmentCheck} type="button">
            检查环境
          </button>
          <button
            className="ghost-button"
            onClick={() => desktopApi.toggle_favorite_current_query({ form }).then(() => desktopApi.get_ui_state().then(setUiState))}
            type="button"
          >
            收藏当前查询
          </button>
          <button className="primary-button" disabled={uiState.status.busy || isPending} onClick={() => handleStartScan()} type="button">
            {uiState.status.busy ? "扫描中…" : "开始比价"}
          </button>
        </div>
      </header>

      <div className="status-strip">
        <div>
          <strong>{uiState.status.message}</strong>
          {uiState.status.progress.total > 0 ? (
            <span>
              {uiState.status.progress.step}/{uiState.status.progress.total}
            </span>
          ) : null}
        </div>
        <div>{actionMessage}</div>
        {uiState.status.busy ? (
          <button className="ghost-button" onClick={() => desktopApi.cancel_scan()} type="button">
            取消
          </button>
        ) : null}
      </div>

      <main className="workspace-grid">
        <aside className="left-column">
          <SectionCard className="query-card" title="查询参数" subtitle="常用参数在上，高级开关在下。">
            <div className="form-grid">
              <label>
                <span>出发地</span>
                <input
                  value={form.origin}
                  onChange={(event) => applyFormPatch({ origin: event.target.value })}
                  onFocus={() => setActiveField("origin")}
                />
                <small>{uiState.hints.origin}</small>
                {activeField === "origin" && suggestions.origin.length ? (
                  <div className="suggestion-list">
                    {suggestions.origin.map((item) => (
                      <button
                        key={`${item.code}-${item.name}`}
                        type="button"
                        onClick={() => {
                          applyFormPatch({ origin: item.name });
                          setSuggestions((current) => ({ ...current, origin: [] }));
                        }}
                      >
                        {item.label}
                      </button>
                    ))}
                  </div>
                ) : null}
              </label>

              <label>
                <span>目的地</span>
                <input
                  value={form.destination}
                  onChange={(event) => applyFormPatch({ destination: event.target.value })}
                  onFocus={() => setActiveField("destination")}
                />
                <small>{uiState.hints.destination}</small>
                {activeField === "destination" && suggestions.destination.length ? (
                  <div className="suggestion-list">
                    {suggestions.destination.map((item) => (
                      <button
                        key={`${item.code}-${item.name}`}
                        type="button"
                        onClick={() => {
                          applyFormPatch({ destination: item.name });
                          setSuggestions((current) => ({ ...current, destination: [] }));
                        }}
                      >
                        {item.label}
                      </button>
                    ))}
                  </div>
                ) : null}
              </label>

              <label>
                <span>行程类型</span>
                <select value={form.trip_type} onChange={(event) => applyFormPatch({ trip_type: event.target.value })}>
                  <option value="one_way">单程</option>
                  <option value="round_trip">往返</option>
                </select>
              </label>

              <label>
                <span>出发日期</span>
                <input type="date" value={form.date} onChange={(event) => applyFormPatch({ date: event.target.value })} />
              </label>

              {form.trip_type === "round_trip" ? (
                <label>
                  <span>返程日期</span>
                  <input
                    type="date"
                    value={form.return_date}
                    onChange={(event) => applyFormPatch({ return_date: event.target.value })}
                  />
                </label>
              ) : null}

              <label>
                <span>等待秒数</span>
                <input value={form.wait} onChange={(event) => applyFormPatch({ wait: event.target.value })} />
              </label>

              <label className="wide">
                <span>额外地区代码</span>
                <input value={form.regions} onChange={(event) => applyFormPatch({ regions: event.target.value })} />
                <small>{uiState.hints.regions}</small>
              </label>

              <label>
                <span>±天数</span>
                <input value={form.date_window} onChange={(event) => applyFormPatch({ date_window: event.target.value })} />
              </label>
            </div>

            <div className="toggle-grid">
              <label><input checked={form.combined_summary} onChange={(event) => applyFormPatch({ combined_summary: event.target.checked })} type="checkbox" /> 保存多日期汇总</label>
              <label><input checked={form.exact_airport} onChange={(event) => applyFormPatch({ exact_airport: event.target.checked })} type="checkbox" /> 严格机场代码</label>
              <label><input checked={form.origin_country} onChange={(event) => applyFormPatch({ origin_country: event.target.checked })} type="checkbox" /> 出发地按国家</label>
              <label><input checked={form.destination_country} onChange={(event) => applyFormPatch({ destination_country: event.target.checked })} type="checkbox" /> 目的地按国家</label>
            </div>
          </SectionCard>

          <SectionCard
            className="sidebar-card"
            title="历史与收藏"
            subtitle="直接应用旧查询，或先载入再重跑。"
            actions={
              <button className="ghost-button" onClick={() => desktopApi.list_history().then(() => desktopApi.get_ui_state().then(setUiState))} type="button">
                刷新
              </button>
            }
          >
            <HistoryList title="收藏路线" records={uiState.history.favorites} onApply={handleApplyHistory} />
            <HistoryList title="最近查询" records={uiState.history.recent} onApply={handleApplyHistory} />
          </SectionCard>
        </aside>

        <section className="center-column">
          <div className="summary-grid">
            <SummaryCard
              accent="gold"
              eyebrow="最低价结论"
              headline={String(cheapestCard.headline ?? "")}
              price={String(cheapestCard.price ?? "")}
              supporting={String(cheapestCard.supporting ?? "")}
              meta={String(cheapestCard.meta ?? "")}
              insight={String(cheapestCard.insight ?? "")}
              buttonText={String(cheapestCard.button_text ?? "等待结果")}
              action={() => cheapestCard.link && desktopApi.open_link(String(cheapestCard.link))}
            />
            <SummaryCard
              accent="stone"
              eyebrow="推荐下单方案"
              headline={String(recommendationCard.headline ?? "")}
              price={String(recommendationCard.price ?? "")}
              supporting={String(recommendationCard.supporting ?? "")}
              meta={String(recommendationCard.meta ?? "")}
              insight={String(recommendationCard.insight ?? "")}
              buttonText={String(recommendationCard.button_text ?? "等待结果")}
              action={() => recommendationCard.link && desktopApi.open_link(String(recommendationCard.link))}
            />
          </div>

          <SectionCard
            className="workspace-card"
            title="结果工作区"
            subtitle="先看结论与 top 方案，再决定是否进入价格日历或失败补扫。"
            actions={
              <>
                <button className="ghost-button" onClick={() => desktopApi.open_outputs()} type="button">
                  打开结果目录
                </button>
                <button className="ghost-button" onClick={() => desktopApi.export_decision_summary()} type="button">
                  导出决策摘要
                </button>
              </>
            }
          >
            <div className="toolbar">
              <ToolbarButton active={!selectedTripLabel && sourceFilter === "all"} label="全部结果" onClick={() => { setSelectedTripLabel(""); setSourceFilter("all"); }} />
              <ToolbarButton active={sourceFilter === "live"} label="仅实时结果" onClick={() => setSourceFilter("live")} />
              <ToolbarButton active={sourceFilter === "bookable"} label="仅可下单" onClick={() => setSourceFilter("bookable")} />
              <ToolbarButton active={showLowestOnly} label="仅最低价候选" onClick={() => setShowLowestOnly((current) => !current)} />
              <ToolbarButton active={showChangedOnly} label="仅价格变化" onClick={() => setShowChangedOnly((current) => !current)} />
            </div>

            <div className="toggle-row">
              <label><input checked={showSuccess} onChange={(event) => setShowSuccess(event.target.checked)} type="checkbox" /> 成功结果</label>
              <label><input checked={showFailure} onChange={(event) => setShowFailure(event.target.checked)} type="checkbox" /> 失败市场</label>
            </div>

            <div className="top-rec-grid">
              <div className="top-rec-list">
                <div className="panel-label">Top 方案</div>
                {uiState.results.topRecommendations.length ? (
                  uiState.results.topRecommendations.map((row, index) => (
                    <button key={`${String(row.date)}-${String(row.region_code)}-${index}`} className="top-rec-item" onClick={() => row.link && desktopApi.open_link(String(row.link))} type="button">
                      <span>{index + 1}</span>
                      <div>
                        <strong>{String(row.region_name ?? "-")}</strong>
                        <small>{String(row.date ?? "-")} · {String(row.route ?? "-")}</small>
                      </div>
                      <em>{formatMoney(row.cheapest_cny_price)}</em>
                    </button>
                  ))
                ) : (
                  <EmptyState text="扫描后会在这里显示最值得优先打开的市场。" />
                )}
              </div>

              <div className="insight-panel">
                <div className="toolbar">
                  <ToolbarButton active={insightTab === "calendar"} label="价格日历" onClick={() => setInsightTab("calendar")} />
                  <ToolbarButton active={insightTab === "compare"} label="历史对比" onClick={() => setInsightTab("compare")} />
                  <ToolbarButton active={insightTab === "history"} label="路线复盘" onClick={() => setInsightTab("history")} />
                  <ToolbarButton active={insightTab === "table"} label="表格聚焦" onClick={() => setInsightTab("table")} />
                </div>

                {insightTab === "calendar" ? (
                  <div className="calendar-panel">
                    <p className="panel-note">{uiState.results.calendar.summaryText ?? "等待扫描后生成价格日历。"}</p>
                    {uiState.results.calendar.kind === "empty" ? (
                      <EmptyState text="当前没有日历数据。" />
                    ) : (
                      <div className={`calendar-grid ${uiState.results.calendar.kind}`}>
                        {uiState.results.calendar.cells.map((cell) => (
                          <button
                            key={cell.tripLabel}
                            className={`calendar-cell ${selectedTripLabel === cell.tripLabel ? "active" : ""}`}
                            onClick={() => setSelectedTripLabel((current) => (current === cell.tripLabel ? "" : cell.tripLabel))}
                            type="button"
                          >
                            <strong>{cell.tripLabel}</strong>
                            <span>{cell.price ? formatMoney(cell.price) : "无价格"}</span>
                            <small>{cell.regionName ?? "-"}</small>
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                ) : null}

                {insightTab === "compare" ? (
                  <div className="compare-list">
                    {uiState.results.compareRows.length ? (
                      uiState.results.compareRows.map((row, index) => (
                        <div key={`${row.date}-${row.region}-${index}`} className="compare-item">
                          <div>
                            <strong>{row.region}</strong>
                            <small>{row.date} · {row.route}</small>
                          </div>
                          <div>
                            <span>{row.current}</span>
                            <span>{row.previous}</span>
                            <em>{row.change}</em>
                          </div>
                        </div>
                      ))
                    ) : (
                      <EmptyState text="暂无历史对比数据。" />
                    )}
                  </div>
                ) : null}

                {insightTab === "history" ? (
                  <pre className="history-detail">{uiState.history.historyDetail}</pre>
                ) : null}

                {insightTab === "table" ? (
                  <p className="panel-note">表格聚焦模式下，使用上方筛选和下方结果表快速定位目标市场。</p>
                ) : null}
              </div>
            </div>

            <SectionCard className="table-card" title="成功结果" subtitle={`${filteredResults.successRows.length} 条可展示价格结果`}>
              <DataTable
                columns={[
                  { key: "date", label: "日期" },
                  { key: "route", label: "航段" },
                  { key: "region_name", label: "地区" },
                  { key: "source_label", label: "来源" },
                  { key: "best_cny_price", label: "最佳价", align: "right" },
                  { key: "cheapest_cny_price", label: "最低价", align: "right" },
                  { key: "delta_label", label: "变化" },
                ]}
                rows={filteredResults.successRows}
                onOpenLink={(url) => desktopApi.open_link(url)}
              />
            </SectionCard>

            <SectionCard
              className="table-card danger-card"
              title="失败市场"
              subtitle={`${filteredResults.failureRows.length} 条需人工补救或复扫`}
              actions={
                <button className="ghost-button" onClick={() => desktopApi.run_retry_queue().then(() => handleStartScan({ rerunScopeOverride: "selected_regions", allowBrowserFallback: false }))} type="button">
                  运行补扫队列
                </button>
              }
            >
              <DataTable
                columns={[
                  { key: "date", label: "日期" },
                  { key: "route", label: "航段" },
                  { key: "region_name", label: "地区" },
                  { key: "failure_category", label: "失败分类" },
                  { key: "failure_action", label: "建议动作" },
                  { key: "status", label: "状态" },
                ]}
                rows={filteredResults.failureRows}
                onOpenLink={(url) => desktopApi.open_link(url)}
                highlightFailure
                onQueueRetry={handleQueueFailure}
              />
            </SectionCard>
          </SectionCard>
        </section>

        <aside className="right-column">
          <SectionCard className="sidebar-card" title="提醒与自动复扫" subtitle={uiState.alerts.summary}>
            <div className="form-grid single-column">
              <label>
                <span>目标价 ≤</span>
                <input value={alertDraft.targetPrice} onChange={(event) => setAlertDraft((current) => ({ ...current, targetPrice: event.target.value }))} />
              </label>
              <label>
                <span>再降 ≥</span>
                <input value={alertDraft.dropAmount} onChange={(event) => setAlertDraft((current) => ({ ...current, dropAmount: event.target.value }))} />
              </label>
              <label>
                <span>自动复扫(分钟)</span>
                <input value={alertDraft.autoRefreshMinutes} onChange={(event) => setAlertDraft((current) => ({ ...current, autoRefreshMinutes: event.target.value }))} />
              </label>
            </div>
            <div className="toggle-grid single-column">
              <label><input checked={alertDraft.notificationsEnabled} onChange={(event) => setAlertDraft((current) => ({ ...current, notificationsEnabled: event.target.checked }))} type="checkbox" /> 启用桌面通知</label>
              <label><input checked={alertDraft.notifyOnRecovery} onChange={(event) => setAlertDraft((current) => ({ ...current, notifyOnRecovery: event.target.checked }))} type="checkbox" /> 通知失败恢复</label>
              <label><input checked={alertDraft.notifyOnNewLow} onChange={(event) => setAlertDraft((current) => ({ ...current, notifyOnNewLow: event.target.checked }))} type="checkbox" /> 通知刷新历史新低</label>
            </div>
            <div className="section-actions flush-top">
              <button className="primary-button subtle" onClick={handleSaveAlerts} type="button">保存设置</button>
              <button className="ghost-button" onClick={() => desktopApi.clear_alert_config({ form }).then(() => desktopApi.get_ui_state().then(setUiState))} type="button">清除</button>
            </div>
          </SectionCard>

          <SectionCard className="sidebar-card" title="环境状态" subtitle="主抓取、回退链路和项目路径。">
            {uiState.environment.lines.length ? (
              <ul className="line-list">
                {uiState.environment.lines.map((line) => <li key={line}>{line}</li>)}
              </ul>
            ) : (
              <EmptyState text="还未执行环境检查。" />
            )}
          </SectionCard>

          <SectionCard className="sidebar-card log-card" title="运行日志" subtitle="保留最近日志，便于排障。">
            {uiState.logs.length ? (
              <div className="log-list">
                {uiState.logs.slice(-18).map((item) => (
                  <div key={`${item.timestamp}-${item.message}`} className="log-item">
                    <span>{item.timestamp}</span>
                    <p>{item.message}</p>
                  </div>
                ))}
              </div>
            ) : (
              <EmptyState text="暂无日志。" />
            )}
          </SectionCard>
        </aside>
      </main>
    </div>
  );
}

export default App;
