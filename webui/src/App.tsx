import {
  startTransition,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
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

const weekdayLabels = ["一", "二", "三", "四", "五", "六", "日"];

function formatMoney(value: unknown): string {
  return typeof value === "number" ? `¥${value.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}` : "-";
}

function parseIsoDate(value: string): Date | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    return null;
  }
  const [year, month, day] = value.split("-").map(Number);
  const date = new Date(year, month - 1, day);
  if (
    Number.isNaN(date.getTime()) ||
    date.getFullYear() !== year ||
    date.getMonth() !== month - 1 ||
    date.getDate() !== day
  ) {
    return null;
  }
  date.setHours(12, 0, 0, 0);
  return date;
}

function formatIsoDate(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function shiftIsoDate(value: string, days: number): string {
  const base = parseIsoDate(value) ?? new Date();
  const shifted = new Date(base);
  shifted.setDate(shifted.getDate() + days);
  return formatIsoDate(shifted);
}

function buildCalendarDays(viewMonth: Date): Date[] {
  const firstDay = new Date(viewMonth.getFullYear(), viewMonth.getMonth(), 1);
  firstDay.setHours(12, 0, 0, 0);
  const startOffset = (firstDay.getDay() + 6) % 7;
  const startDate = new Date(firstDay);
  startDate.setDate(firstDay.getDate() - startOffset);
  return Array.from({ length: 42 }, (_, index) => {
    const cell = new Date(startDate);
    cell.setDate(startDate.getDate() + index);
    return cell;
  });
}

function DateField({
  label,
  value,
  onChange,
  min,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  min?: string;
}) {
  const rootRef = useRef<HTMLDivElement>(null);
  const parsedValue = parseIsoDate(value);
  const minDate = min ? parseIsoDate(min) : null;
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(value);
  const [viewMonth, setViewMonth] = useState<Date>(parsedValue ?? minDate ?? new Date());
  const calendarDays = useMemo(() => buildCalendarDays(viewMonth), [viewMonth]);

  useEffect(() => {
    const nextParsedValue = parseIsoDate(value);
    setDraft(value);
    if (nextParsedValue) {
      setViewMonth(nextParsedValue);
    }
  }, [value]);

  useClickOutside(rootRef, () => setOpen(false));

  const commitDraft = () => {
    const nextValue = draft.trim();
    const parsed = parseIsoDate(nextValue);
    if (!parsed) {
      setDraft(value);
      return;
    }
    if (minDate && parsed < minDate) {
      const clamped = formatIsoDate(minDate);
      setDraft(clamped);
      onChange(clamped);
      return;
    }
    onChange(nextValue);
  };

  const applyQuickShift = (days: number) => {
    const nextValue = shiftIsoDate(value || formatIsoDate(new Date()), days);
    if (minDate && parseIsoDate(nextValue)! < minDate) {
      onChange(formatIsoDate(minDate));
      return;
    }
    onChange(nextValue);
  };

  return (
    <div className="date-field" ref={rootRef}>
      <label className="block text-xs font-medium tracking-wider uppercase text-stone-400 mb-1.5">{label}</label>
      <div className="date-input-shell">
        <input
          className="date-text-input"
          inputMode="numeric"
          placeholder="YYYY-MM-DD"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commitDraft}
          onFocus={() => setOpen(true)}
        />
        <button className="date-picker-toggle" type="button" onClick={() => setOpen((current) => !current)}>
          选日期
        </button>
      </div>
      <div className="date-quick-actions">
        <button type="button" onClick={() => applyQuickShift(-1)}>前一天</button>
        <button type="button" onClick={() => applyQuickShift(1)}>后一天</button>
        <button type="button" onClick={() => onChange(formatIsoDate(new Date()))}>今天</button>
      </div>
      {open ? (
        <div className="date-popover">
          <div className="date-popover-head">
            <strong>{viewMonth.getFullYear()}年{viewMonth.getMonth() + 1}月</strong>
            <div className="date-popover-nav">
              <button
                type="button"
                onClick={() => setViewMonth(new Date(viewMonth.getFullYear(), viewMonth.getMonth() - 1, 1))}
              >
                上月
              </button>
              <button
                type="button"
                onClick={() => setViewMonth(new Date(viewMonth.getFullYear(), viewMonth.getMonth() + 1, 1))}
              >
                下月
              </button>
            </div>
          </div>
          <div className="date-weekdays">
            {weekdayLabels.map((weekday) => (
              <span key={weekday}>{weekday}</span>
            ))}
          </div>
          <div className="date-grid">
            {calendarDays.map((day) => {
              const isoDate = formatIsoDate(day);
              const inMonth = day.getMonth() === viewMonth.getMonth();
              const disabled = Boolean(minDate && day < minDate);
              return (
                <button
                  key={isoDate}
                  type="button"
                  className={`date-grid-cell ${inMonth ? "" : "muted"} ${isoDate === value ? "active" : ""}`}
                  disabled={disabled}
                  onClick={() => {
                    onChange(isoDate);
                    setOpen(false);
                  }}
                >
                  {day.getDate()}
                </button>
              );
            })}
          </div>
        </div>
      ) : null}
    </div>
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

function Switch({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={() => onChange(!checked)}
      className="group flex items-center gap-3 cursor-pointer select-none"
    >
      <span
        className={
          "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full border transition-colors duration-200 " +
          (checked
            ? "bg-stone-900 border-stone-900"
            : "bg-stone-200 border-stone-300")
        }
      >
        <span
          className={
            "inline-block h-4 w-4 rounded-full bg-white shadow transition-transform duration-200 " +
            (checked ? "translate-x-5" : "translate-x-1")
          }
        />
      </span>
      <span className="text-sm text-stone-600">{label}</span>
    </button>
  );
}

function Collapsible({
  open,
  onToggle,
  trigger,
  children,
}: {
  open: boolean;
  onToggle: () => void;
  trigger: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div>
      <button
        type="button"
        onClick={onToggle}
        className="flex items-center gap-1 text-sm text-stone-500 hover:text-stone-800 transition-colors cursor-pointer"
      >
        {trigger}
        <svg
          className={`w-4 h-4 transition-transform duration-200 ${open ? "rotate-180" : ""}`}
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      <div
        className={`grid transition-all duration-300 ease-out ${open ? "grid-rows-[1fr] opacity-100 mt-6 pt-6 border-t border-stone-100" : "grid-rows-[0fr] opacity-0 mt-0 pt-0 border-t border-transparent"}`}
      >
        <div className="overflow-hidden">{children}</div>
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
                      <button className="toolbar-button" onClick={() => onOpenLink(String(row.link))} type="button">
                        打开
                      </button>
                    ) : null}
                    {highlightFailure && onQueueRetry ? (
                      <button className="toolbar-button" onClick={() => onQueueRetry(row)} type="button">
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

function useClickOutside(ref: React.RefObject<HTMLElement | null>, handler: () => void) {
  useEffect(() => {
    const listener = (event: MouseEvent) => {
      if (!ref.current || ref.current.contains(event.target as Node)) {
        return;
      }
      handler();
    };
    document.addEventListener("mousedown", listener);
    return () => document.removeEventListener("mousedown", listener);
  }, [ref, handler]);
}

function App() {
  const [uiState, setUiState] = useState<UIState | null>(null);
  const [form, setForm] = useState<FormState | null>(null);
  const [bootstrapError, setBootstrapError] = useState("");
  const [alertDraft, setAlertDraft] = useState<AlertDraft>(defaultAlertDraft);
  const [leftDrawerOpen, setLeftDrawerOpen] = useState(false);
  const [rightDrawerOpen, setRightDrawerOpen] = useState(false);
  const [showDetails, setShowDetails] = useState(false);
  const [detailTab, setDetailTab] = useState<"table" | "calendar" | "compare" | "history">("table");
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
  const [advancedOpen, setAdvancedOpen] = useState(false);

  const originDeferred = useDeferredValue(form?.origin ?? "");
  const destinationDeferred = useDeferredValue(form?.destination ?? "");
  const originRef = useRef<HTMLDivElement>(null);
  const destRef = useRef<HTMLDivElement>(null);

  useClickOutside(originRef, () => {
    if (activeField === "origin") {
      setActiveField(null);
      setSuggestions((c) => ({ ...c, origin: [] }));
    }
  });
  useClickOutside(destRef, () => {
    if (activeField === "destination") {
      setActiveField(null);
      setSuggestions((c) => ({ ...c, destination: [] }));
    }
  });

  useEffect(() => {
    let cancelled = false;
    desktopApi
      .get_initial_state()
      .then((state) => {
        if (cancelled) return;
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
        if (cancelled) return;
        setBootstrapError(error instanceof Error ? error.message : "桌面桥接初始化失败。");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!uiState) return;
    const timer = window.setInterval(() => {
      desktopApi.get_ui_state().then((nextState) => {
        startTransition(() => {
          setUiState(nextState);
        });
      });
    }, 1000);
    return () => window.clearInterval(timer);
  }, [uiState]);

  useEffect(() => {
    if (!form) return;
    const timer = window.setTimeout(() => {
      desktopApi.update_query_state(form).catch(() => undefined);
    }, 400);
    return () => window.clearTimeout(timer);
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
    if (!showSuccess) successRows = [];
    if (!showFailure) failureRows = [];
    return { successRows, failureRows };
  }, [selectedTripLabel, showChangedOnly, showFailure, showLowestOnly, showSuccess, sourceFilter, uiState]);

  if (bootstrapError) {
    return <BootScreen title="桌面桥接初始化失败" detail={bootstrapError} error />;
  }

  if (!uiState || !form) {
    return <BootScreen title="正在连接桌面服务" detail="首次启动会稍慢一些。" />;
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
    setLeftDrawerOpen(false);
  };

  const handleSaveAlerts = async () => {
    try {
      await desktopApi.save_alert_config({ form, ...alertDraft });
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
  const hasAnyResults =
    uiState.results.successRows.length > 0 ||
    uiState.results.failureRows.length > 0 ||
    uiState.results.topRecommendations.length > 0;

  const filterSummaryParts: string[] = [];
  if (sourceFilter !== "all") filterSummaryParts.push(sourceFilter === "live" ? "仅实时" : "仅可下单");
  if (showLowestOnly) filterSummaryParts.push("仅最低价");
  if (showChangedOnly) filterSummaryParts.push("仅变化");
  if (selectedTripLabel) filterSummaryParts.push(selectedTripLabel);

  return (
    <div className="min-h-dvh flex flex-col">
      {/* Topbar ---------------------------------------------------------- */}
      <header className="shrink-0 flex items-center justify-between gap-3 px-4 py-3 border-b border-stone-200/60">
        <button
          className="px-3 py-2 rounded-xl border border-stone-200 bg-white/70 text-stone-500 text-sm hover:text-stone-800 hover:border-stone-300 transition cursor-pointer"
          onClick={() => setLeftDrawerOpen(true)}
          type="button"
        >
          历史
        </button>
        <span className="text-base font-semibold tracking-tight text-stone-900">Skyscanner</span>
        <button
          className="px-3 py-2 rounded-xl border border-stone-200 bg-white/70 text-stone-500 text-sm hover:text-stone-800 hover:border-stone-300 transition cursor-pointer"
          onClick={() => setRightDrawerOpen(true)}
          type="button"
        >
          设置
        </button>
      </header>

      {/* Main ------------------------------------------------------------ */}
      <main className={`flex-1 flex flex-col gap-5 px-4 py-6 ${hasAnyResults ? "" : "items-center justify-center"}`}>
        {/* Query Card ------------------------------------------------------ */}
        <div className="w-full max-w-2xl mx-auto p-8 md:p-10 bg-[#FFFCF7]/90 backdrop-blur-md rounded-3xl shadow-[0_20px_60px_-15px_rgba(63,44,18,0.05)] border border-stone-100">
          {/* Core four-square grid */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-6">
            <div className="relative" ref={originRef}>
              <label className="block text-xs font-medium tracking-wider uppercase text-stone-400 mb-1.5">出发地</label>
              <input
                className="w-full rounded-xl border border-stone-200 bg-white px-3.5 py-2.5 text-stone-800 placeholder:text-stone-300 outline-none transition focus-visible:ring-2 focus-visible:ring-stone-400/20 focus-visible:border-stone-300"
                value={form.origin}
                onChange={(e) => applyFormPatch({ origin: e.target.value })}
                onFocus={() => setActiveField("origin")}
                placeholder="例如：北京"
              />
              {activeField === "origin" && suggestions.origin.length > 0 && (
                <div className="suggestion-list">
                  {suggestions.origin.map((item) => (
                    <button
                      key={`${item.code}-${item.name}`}
                      type="button"
                      onClick={() => {
                        applyFormPatch({ origin: item.name });
                        setSuggestions((c) => ({ ...c, origin: [] }));
                      }}
                    >
                      {item.label}
                    </button>
                  ))}
                </div>
              )}
            </div>

            <div className="relative" ref={destRef}>
              <label className="block text-xs font-medium tracking-wider uppercase text-stone-400 mb-1.5">目的地</label>
              <input
                className="w-full rounded-xl border border-stone-200 bg-white px-3.5 py-2.5 text-stone-800 placeholder:text-stone-300 outline-none transition focus-visible:ring-2 focus-visible:ring-stone-400/20 focus-visible:border-stone-300"
                value={form.destination}
                onChange={(e) => applyFormPatch({ destination: e.target.value })}
                onFocus={() => setActiveField("destination")}
                placeholder="例如：东京"
              />
              {activeField === "destination" && suggestions.destination.length > 0 && (
                <div className="suggestion-list">
                  {suggestions.destination.map((item) => (
                    <button
                      key={`${item.code}-${item.name}`}
                      type="button"
                      onClick={() => {
                        applyFormPatch({ destination: item.name });
                        setSuggestions((c) => ({ ...c, destination: [] }));
                      }}
                    >
                      {item.label}
                    </button>
                  ))}
                </div>
              )}
            </div>

            <div>
              <label className="block text-xs font-medium tracking-wider uppercase text-stone-400 mb-1.5">行程</label>
              <select
                className="w-full rounded-xl border border-stone-200 bg-white px-3.5 py-2.5 text-stone-800 outline-none transition focus-visible:ring-2 focus-visible:ring-stone-400/20 focus-visible:border-stone-300 cursor-pointer"
                value={form.trip_type}
                onChange={(e) => applyFormPatch({ trip_type: e.target.value })}
              >
                <option value="one_way">单程</option>
                <option value="round_trip">往返</option>
              </select>
            </div>

            <DateField label="出发日期" value={form.date} onChange={(value) => applyFormPatch({ date: value })} />

            {form.trip_type === "round_trip" ? (
              <div className="sm:col-span-2">
                <DateField
                  label="返程日期"
                  value={form.return_date}
                  min={form.date}
                  onChange={(value) => applyFormPatch({ return_date: value })}
                />
              </div>
            ) : null}
          </div>

          {/* Advanced collapsible */}
          <div className="mt-6">
            <Collapsible
              open={advancedOpen}
              onToggle={() => setAdvancedOpen((c) => !c)}
              trigger={<span>高级搜索设置</span>}
            >
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-6">
                <div>
                  <label className="block text-xs font-medium tracking-wider uppercase text-stone-400 mb-1.5">等待秒数</label>
                  <input
                    className="w-full rounded-xl border border-stone-200 bg-white px-3.5 py-2.5 text-stone-800 outline-none transition focus-visible:ring-2 focus-visible:ring-stone-400/20 focus-visible:border-stone-300"
                    value={form.wait}
                    onChange={(e) => applyFormPatch({ wait: e.target.value })}
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium tracking-wider uppercase text-stone-400 mb-1.5">±天数</label>
                  <input
                    className="w-full rounded-xl border border-stone-200 bg-white px-3.5 py-2.5 text-stone-800 outline-none transition focus-visible:ring-2 focus-visible:ring-stone-400/20 focus-visible:border-stone-300"
                    value={form.date_window}
                    onChange={(e) => applyFormPatch({ date_window: e.target.value })}
                  />
                </div>
                <div className="sm:col-span-2">
                  <label className="block text-xs font-medium tracking-wider uppercase text-stone-400 mb-1.5">额外地区</label>
                  <input
                    className="w-full rounded-xl border border-stone-200 bg-white px-3.5 py-2.5 text-stone-800 outline-none transition focus-visible:ring-2 focus-visible:ring-stone-400/20 focus-visible:border-stone-300"
                    value={form.regions}
                    onChange={(e) => applyFormPatch({ regions: e.target.value })}
                  />
                  <p className="text-xs text-stone-400 mt-1.5">{uiState.hints.regions}</p>
                </div>
              </div>
            </Collapsible>
          </div>

          {/* Switches + CTA */}
          <div className="mt-10 pt-6 border-t border-stone-100/60 flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-5">
            <div className="flex flex-wrap gap-x-6 gap-y-3">
              <Switch checked={form.combined_summary} onChange={(v) => applyFormPatch({ combined_summary: v })} label="保存汇总" />
              <Switch checked={form.exact_airport} onChange={(v) => applyFormPatch({ exact_airport: v })} label="严格机场" />
              <Switch checked={form.origin_country} onChange={(v) => applyFormPatch({ origin_country: v })} label="出发按国家" />
              <Switch checked={form.destination_country} onChange={(v) => applyFormPatch({ destination_country: v })} label="目的按国家" />
            </div>
            <button
              className="shrink-0 px-8 py-3 rounded-full bg-stone-900 text-white text-sm font-medium shadow-md hover:-translate-y-0.5 hover:bg-stone-800 active:translate-y-0 transition disabled:opacity-50 disabled:cursor-not-allowed disabled:transform-none cursor-pointer"
              disabled={uiState.status.busy || isPending}
              onClick={() => handleStartScan()}
              type="button"
            >
              {uiState.status.busy ? "扫描中…" : "开始比价"}
            </button>
          </div>
        </div>

        {/* Results --------------------------------------------------------- */}
        {hasAnyResults && (
          <div className="result-stream w-full max-w-2xl mx-auto">
            <section className="summary-card">
              <p className="eyebrow">最低价结论</p>
              <h2>{String(cheapestCard.headline ?? "-")}</h2>
              <div className="summary-price">{String(cheapestCard.price ?? "-")}</div>
              <p className="summary-supporting">{String(cheapestCard.supporting ?? "")}</p>
              <p className="summary-meta">{String(cheapestCard.meta ?? "")}</p>
              <p className="summary-insight">{String(cheapestCard.insight ?? "")}</p>
              {cheapestCard.link ? (
                <button className="primary-button subtle" onClick={() => desktopApi.open_link(String(cheapestCard.link))} type="button">
                  {String(cheapestCard.button_text ?? "打开链接")}
                </button>
              ) : null}
            </section>

            <section className="summary-card alt">
              <p className="eyebrow">推荐下单方案</p>
              <h2>{String(recommendationCard.headline ?? "-")}</h2>
              <div className="summary-price">{String(recommendationCard.price ?? "-")}</div>
              <p className="summary-supporting">{String(recommendationCard.supporting ?? "")}</p>
              <p className="summary-meta">{String(recommendationCard.meta ?? "")}</p>
              <p className="summary-insight">{String(recommendationCard.insight ?? "")}</p>
              {recommendationCard.link ? (
                <button className="primary-button subtle" onClick={() => desktopApi.open_link(String(recommendationCard.link))} type="button">
                  {String(recommendationCard.button_text ?? "打开链接")}
                </button>
              ) : null}
            </section>

            {uiState.results.topRecommendations.length > 0 && (
              <div className="top-rec-panel">
                <div className="panel-label">Top 方案</div>
                <div className="top-rec-list">
                  {uiState.results.topRecommendations.map((row, index) => (
                    <button
                      key={`${String(row.date)}-${String(row.region_code)}-${index}`}
                      className="top-rec-item"
                      onClick={() => row.link && desktopApi.open_link(String(row.link))}
                      type="button"
                    >
                      <span>{index + 1}</span>
                      <div>
                        <strong>{String(row.region_name ?? "-")}</strong>
                        <small>{String(row.date ?? "-")} · {String(row.route ?? "-")}</small>
                      </div>
                      <em>{formatMoney(row.cheapest_cny_price)}</em>
                    </button>
                  ))}
                </div>
              </div>
            )}

            {!showDetails ? (
              <button className="ghost-button wide-button" onClick={() => setShowDetails(true)} type="button">
                查看详细结果
              </button>
            ) : (
              <div className="details-panel">
                <div className="toolbar">
                  <ToolbarButton active={detailTab === "table"} label="表格" onClick={() => setDetailTab("table")} />
                  <ToolbarButton active={detailTab === "calendar"} label="日历" onClick={() => setDetailTab("calendar")} />
                  <ToolbarButton active={detailTab === "compare"} label="对比" onClick={() => setDetailTab("compare")} />
                  <ToolbarButton active={detailTab === "history"} label="历史" onClick={() => setDetailTab("history")} />
                </div>

                {detailTab === "table" && (
                  <>
                    <div className="filter-bar">
                      <div className="filter-chips">
                        <ToolbarButton active={!selectedTripLabel && sourceFilter === "all"} label="全部" onClick={() => { setSelectedTripLabel(""); setSourceFilter("all"); }} />
                        <ToolbarButton active={sourceFilter === "live"} label="仅实时" onClick={() => setSourceFilter("live")} />
                        <ToolbarButton active={sourceFilter === "bookable"} label="仅可下单" onClick={() => setSourceFilter("bookable")} />
                        <ToolbarButton active={showLowestOnly} label="仅最低价" onClick={() => setShowLowestOnly((c) => !c)} />
                        <ToolbarButton active={showChangedOnly} label="仅变化" onClick={() => setShowChangedOnly((c) => !c)} />
                      </div>
                      {filterSummaryParts.length > 0 && (
                        <div className="filter-summary">
                          <span>{filterSummaryParts.join(" · ")}</span>
                          <button className="text-button" onClick={() => { setSelectedTripLabel(""); setSourceFilter("all"); setShowLowestOnly(false); setShowChangedOnly(false); }} type="button">清除</button>
                        </div>
                      )}
                      <div className="toggle-row">
                        <label><input checked={showSuccess} onChange={(e) => setShowSuccess(e.target.checked)} type="checkbox" /> 成功</label>
                        <label><input checked={showFailure} onChange={(e) => setShowFailure(e.target.checked)} type="checkbox" /> 失败</label>
                      </div>
                    </div>

                    <div className="table-section">
                      <h4>成功结果 <small>{filteredResults.successRows.length}</small></h4>
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
                    </div>

                    <div className="table-section">
                      <h4>
                        失败市场 <small>{filteredResults.failureRows.length}</small>
                        <button
                          className="text-button"
                          onClick={() => desktopApi.run_retry_queue().then(() => handleStartScan({ rerunScopeOverride: "selected_regions", allowBrowserFallback: false }))}
                          type="button"
                        >
                          运行补扫队列
                        </button>
                      </h4>
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
                    </div>
                  </>
                )}

                {detailTab === "calendar" && (
                  <div className="calendar-panel">
                    <p className="panel-note">{uiState.results.calendar.summaryText ?? "选择日期组合进行筛选。"}</p>
                    {uiState.results.calendar.kind === "empty" ? (
                      <EmptyState text="当前没有日历数据。" />
                    ) : (
                      <div className={`calendar-grid ${uiState.results.calendar.kind}`}>
                        {uiState.results.calendar.cells.map((cell) => (
                          <button
                            key={cell.tripLabel}
                            className={`calendar-cell ${selectedTripLabel === cell.tripLabel ? "active" : ""}`}
                            onClick={() => setSelectedTripLabel((c) => (c === cell.tripLabel ? "" : cell.tripLabel))}
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
                )}

                {detailTab === "compare" && (
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
                )}

                {detailTab === "history" && (
                  <pre className="history-detail">{uiState.history.historyDetail}</pre>
                )}

                <button className="ghost-button wide-button" onClick={() => setShowDetails(false)} type="button">
                  收起详细结果
                </button>
              </div>
            )}
          </div>
        )}
      </main>

      {/* Status bar ------------------------------------------------------ */}
      <footer className="shrink-0 flex items-center justify-between gap-3 px-4 py-3 border-t border-stone-200/60">
        <div className="flex items-center gap-2.5">
          <span
            className={
              "inline-block w-2 h-2 rounded-full " +
              (uiState.environment.lines.length > 0 ? "bg-green-600" : "bg-stone-300")
            }
          />
          <span className="text-sm text-stone-500">{uiState.status.message}</span>
          {uiState.status.progress.total > 0 ? (
            <span className="text-xs text-stone-400">
              {uiState.status.progress.step}/{uiState.status.progress.total}
            </span>
          ) : null}
        </div>
        <div className="flex items-center gap-2">
          {actionMessage ? <span className="text-sm text-stone-500">{actionMessage}</span> : null}
          {uiState.status.busy ? (
            <button
              className="px-3 py-1.5 rounded-lg border border-stone-200 bg-white/70 text-stone-500 text-sm hover:text-stone-800 hover:border-stone-300 transition cursor-pointer"
              onClick={() => desktopApi.cancel_scan()}
              type="button"
            >
              取消
            </button>
          ) : null}
        </div>
      </footer>

      {/* Drawers --------------------------------------------------------- */}
      {leftDrawerOpen && (
        <div className="drawer-overlay" onClick={() => setLeftDrawerOpen(false)}>
          <aside className="drawer drawer-left" onClick={(e) => e.stopPropagation()}>
            <div className="drawer-header">
              <h3>历史与收藏</h3>
              <button className="toolbar-button" onClick={() => setLeftDrawerOpen(false)} type="button">关闭</button>
            </div>
            <div className="drawer-body">
              <HistoryList title="收藏路线" records={uiState.history.favorites} onApply={handleApplyHistory} />
              <HistoryList title="最近查询" records={uiState.history.recent} onApply={handleApplyHistory} />
              <div className="drawer-actions">
                <button className="toolbar-button" onClick={() => desktopApi.open_outputs()} type="button">打开结果目录</button>
                <button className="toolbar-button" onClick={() => desktopApi.export_decision_summary()} type="button">导出决策摘要</button>
                <button className="toolbar-button" onClick={() => desktopApi.toggle_favorite_current_query({ form }).then(() => desktopApi.get_ui_state().then(setUiState))} type="button">收藏当前查询</button>
                <button className="toolbar-button" onClick={() => desktopApi.list_history().then(() => desktopApi.get_ui_state().then(setUiState))} type="button">刷新历史</button>
              </div>
            </div>
          </aside>
        </div>
      )}

      {rightDrawerOpen && (
        <div className="drawer-overlay" onClick={() => setRightDrawerOpen(false)}>
          <aside className="drawer drawer-right" onClick={(e) => e.stopPropagation()}>
            <div className="drawer-header">
              <h3>设置</h3>
              <button className="toolbar-button" onClick={() => setRightDrawerOpen(false)} type="button">关闭</button>
            </div>
            <div className="drawer-body">
              <div className="drawer-section">
                <h4>提醒与自动复扫</h4>
                <p className="drawer-hint">{uiState.alerts.summary}</p>
                <div className="grid gap-3">
                  <label className="block text-xs font-medium tracking-wider uppercase text-stone-400 mb-1">目标价 ≤</label>
                  <input className="w-full rounded-xl border border-stone-200 bg-white px-3.5 py-2.5 text-stone-800 outline-none transition focus-visible:ring-2 focus-visible:ring-stone-400/20 focus-visible:border-stone-300" value={alertDraft.targetPrice} onChange={(e) => setAlertDraft((c) => ({ ...c, targetPrice: e.target.value }))} />
                  <label className="block text-xs font-medium tracking-wider uppercase text-stone-400 mb-1">再降 ≥</label>
                  <input className="w-full rounded-xl border border-stone-200 bg-white px-3.5 py-2.5 text-stone-800 outline-none transition focus-visible:ring-2 focus-visible:ring-stone-400/20 focus-visible:border-stone-300" value={alertDraft.dropAmount} onChange={(e) => setAlertDraft((c) => ({ ...c, dropAmount: e.target.value }))} />
                  <label className="block text-xs font-medium tracking-wider uppercase text-stone-400 mb-1">自动复扫(分钟)</label>
                  <input className="w-full rounded-xl border border-stone-200 bg-white px-3.5 py-2.5 text-stone-800 outline-none transition focus-visible:ring-2 focus-visible:ring-stone-400/20 focus-visible:border-stone-300" value={alertDraft.autoRefreshMinutes} onChange={(e) => setAlertDraft((c) => ({ ...c, autoRefreshMinutes: e.target.value }))} />
                </div>
                <div className="mt-4 flex flex-col gap-3">
                  <Switch checked={alertDraft.notificationsEnabled} onChange={(v) => setAlertDraft((c) => ({ ...c, notificationsEnabled: v }))} label="启用桌面通知" />
                  <Switch checked={alertDraft.notifyOnRecovery} onChange={(v) => setAlertDraft((c) => ({ ...c, notifyOnRecovery: v }))} label="通知失败恢复" />
                  <Switch checked={alertDraft.notifyOnNewLow} onChange={(v) => setAlertDraft((c) => ({ ...c, notifyOnNewLow: v }))} label="通知刷新历史新低" />
                </div>
                <div className="drawer-actions">
                  <button className="toolbar-button active" onClick={handleSaveAlerts} type="button">保存设置</button>
                  <button className="toolbar-button" onClick={() => desktopApi.clear_alert_config({ form }).then(() => desktopApi.get_ui_state().then(setUiState))} type="button">清除</button>
                </div>
              </div>

              <div className="drawer-section">
                <h4>环境状态</h4>
                <button className="toolbar-button" onClick={handleEnvironmentCheck} type="button">检查环境</button>
                {uiState.environment.lines.length ? (
                  <ul className="line-list">
                    {uiState.environment.lines.map((line) => <li key={line}>{line}</li>)}
                  </ul>
                ) : (
                  <EmptyState text="还未执行环境检查。" />
                )}
              </div>

              <div className="drawer-section">
                <h4>运行日志</h4>
                {uiState.logs.length ? (
                  <div className="log-list">
                    {uiState.logs.slice(-30).map((item) => (
                      <div key={`${item.timestamp}-${item.message}`} className="log-item">
                        <span>{item.timestamp}</span>
                        <p>{item.message}</p>
                      </div>
                    ))}
                  </div>
                ) : (
                  <EmptyState text="暂无日志。" />
                )}
              </div>
            </div>
          </aside>
        </div>
      )}
    </div>
  );
}

export default App;
