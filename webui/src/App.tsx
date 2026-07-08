import {
  startTransition,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { desktopApi } from "./desktopApi";
import {
  Drawers,
  defaultAlertDraft,
  defaultBackgroundScheduleDraft,
  type AlertDraft,
  type BackgroundScheduleDraft,
} from "./components/Drawer";
import { QueryCard, type SuggestionMap } from "./components/QueryCard";
import { ResultStream } from "./components/ResultStream";
import { StatusBar } from "./components/StatusBar";
import type { FormState, HistoryRecord, ResultRow, UIState } from "./types";

const emptySuggestions: SuggestionMap = {
  origin: [],
  destination: [],
};

function planProgressText(progress: UIState["status"]["progress"]): string {
  const phase = String(progress.active_plan_phase ?? progress.plan_phase ?? "").trim();
  const batchId = progress.plan_batch_id;
  const batchCount = progress.plan_batch_count;
  if (!phase && !batchId && !batchCount) return "";
  const batchText =
    typeof batchId === "number" && typeof batchCount === "number"
      ? `批次 ${batchId}/${batchCount}`
      : "";
  const reason = String(progress.plan_batch_reason ?? "").trim();
  return [phase ? `阶段 ${phase}` : "", batchText, reason].filter(Boolean).join(" · ");
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
        <p className="loading-kicker">{error ? "Startup Error" : "Desktop Loading"}</p>
        <h1>{title}</h1>
        <p>{detail}</p>
      </div>
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
  const [backgroundScheduleDraft, setBackgroundScheduleDraft] = useState<BackgroundScheduleDraft>(defaultBackgroundScheduleDraft);
  const [leftDrawerOpen, setLeftDrawerOpen] = useState(false);
  const [rightDrawerOpen, setRightDrawerOpen] = useState(false);
  const [showDetails, setShowDetails] = useState(false);
  const [detailTab, setDetailTab] = useState<"calendar" | "compare" | "history">("calendar");
  const [showRawResults, setShowRawResults] = useState(false);
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
          autoRefreshMode: state.alerts.config?.auto_refresh_mode ?? "app",
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

  const handleRepairAction = async (payload: Record<string, unknown>) => {
    setIsPending(true);
    try {
      const result = await desktopApi.apply_repair_action(payload);
      const nextState = await desktopApi.get_ui_state();
      setUiState(nextState);
      setForm(nextState.form);
      setActionMessage(result.action === "run_retry" || result.action === "extend_wait" ? "已开始修复补扫。" : "修复动作已应用。");
    } catch (error) {
      setActionMessage(error instanceof Error ? error.message : "修复动作失败。");
    } finally {
      setIsPending(false);
      resetActionMessage();
    }
  };

  const handlePriceConfirmation = async (row: ResultRow, status: "confirmed" | "mismatched") => {
    try {
      await desktopApi.record_price_confirmation({ row, status });
      const nextState = await desktopApi.get_ui_state();
      setUiState(nextState);
      setActionMessage(status === "confirmed" ? "已记录确认样本。" : "已记录价格不符样本。");
    } catch (error) {
      setActionMessage(error instanceof Error ? error.message : "记录确认样本失败。");
    }
    resetActionMessage();
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

  const handleInstallBackgroundSchedule = async () => {
    try {
      const result = await desktopApi.install_background_auto_refresh({
        intervalMinutes: backgroundScheduleDraft.intervalMinutes,
        limit: 1,
        onlyOnAcPower: backgroundScheduleDraft.onlyOnAcPower,
      });
      const interval = String(result.intervalMinutes ?? backgroundScheduleDraft.intervalMinutes);
      setBackgroundScheduleDraft((current) => ({ ...current, intervalMinutes: interval }));
      setActionMessage(`后台调度已安装：每 ${interval} 分钟检查一次。`);
    } catch (error) {
      setActionMessage(error instanceof Error ? error.message : "后台调度安装失败。");
    }
    resetActionMessage();
  };

  const handleUninstallBackgroundSchedule = async () => {
    try {
      await desktopApi.uninstall_background_auto_refresh();
      setActionMessage("后台调度已卸载。");
    } catch (error) {
      setActionMessage(error instanceof Error ? error.message : "后台调度卸载失败。");
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

  const progressText = planProgressText(uiState.status.progress);
  const hasAnyResults =
    uiState.results.successRows.length > 0 ||
    uiState.results.failureRows.length > 0 ||
    uiState.results.topRecommendations.length > 0;

  return (
    <div className="app-shell">
      {/* Topbar ---------------------------------------------------------- */}
      <header className="app-topbar">
        <button
          className="topbar-button"
          onClick={() => setLeftDrawerOpen(true)}
          type="button"
        >
          历史
        </button>
        <span className="text-base font-semibold text-stone-900">Skyscanner</span>
        <button
          className="topbar-button"
          onClick={() => setRightDrawerOpen(true)}
          type="button"
        >
          设置
        </button>
      </header>

      {/* Main ------------------------------------------------------------ */}
      <main className={`flex-1 flex flex-col gap-5 px-4 py-6 ${hasAnyResults ? "" : "items-center justify-center"}`}>
        {/* Query Card ------------------------------------------------------ */}
        <QueryCard
          form={form}
          hintsRegions={uiState.hints.regions}
          suggestions={suggestions}
          activeField={activeField}
          originRef={originRef}
          destRef={destRef}
          advancedOpen={advancedOpen}
          busy={uiState.status.busy}
          isPending={isPending}
          onActiveFieldChange={setActiveField}
          onFormPatch={applyFormPatch}
          onSuggestionsChange={setSuggestions}
          onAdvancedOpenChange={setAdvancedOpen}
          onStartScan={() => handleStartScan()}
        />

        {/* Results --------------------------------------------------------- */}
        {hasAnyResults && (
          <ResultStream
            results={uiState.results}
            historyDetail={uiState.history.historyDetail}
            filteredResults={filteredResults}
            showDetails={showDetails}
            detailTab={detailTab}
            showRawResults={showRawResults}
            showSuccess={showSuccess}
            showFailure={showFailure}
            showChangedOnly={showChangedOnly}
            showLowestOnly={showLowestOnly}
            sourceFilter={sourceFilter}
            selectedTripLabel={selectedTripLabel}
            onShowDetailsChange={setShowDetails}
            onDetailTabChange={setDetailTab}
            onShowRawResultsChange={setShowRawResults}
            onShowSuccessChange={setShowSuccess}
            onShowFailureChange={setShowFailure}
            onShowChangedOnlyChange={setShowChangedOnly}
            onShowLowestOnlyChange={setShowLowestOnly}
            onSourceFilterChange={setSourceFilter}
            onSelectedTripLabelChange={setSelectedTripLabel}
            onOpenLink={(url) => desktopApi.open_link(url)}
            onConfirmPrice={handlePriceConfirmation}
            onQueueFailure={handleQueueFailure}
            onRepairAction={handleRepairAction}
          />
        )}
      </main>

      <StatusBar
        status={uiState.status}
        environmentLines={uiState.environment.lines}
        actionMessage={actionMessage}
        progressText={progressText}
        onOpenSettings={() => setRightDrawerOpen(true)}
        onCancel={() => desktopApi.cancel_scan()}
      />

      {/* Drawers --------------------------------------------------------- */}
      <Drawers
        leftOpen={leftDrawerOpen}
        rightOpen={rightDrawerOpen}
        uiState={uiState}
        alertDraft={alertDraft}
        backgroundScheduleDraft={backgroundScheduleDraft}
        onCloseLeft={() => setLeftDrawerOpen(false)}
        onCloseRight={() => setRightDrawerOpen(false)}
        onAlertDraftChange={setAlertDraft}
        onBackgroundScheduleDraftChange={setBackgroundScheduleDraft}
        onApplyHistory={handleApplyHistory}
        onOpenOutputs={() => desktopApi.open_outputs()}
        onExportDecisionSummary={() => desktopApi.export_decision_summary()}
        onToggleFavorite={() => desktopApi.toggle_favorite_current_query({ form }).then(() => desktopApi.get_ui_state().then(setUiState))}
        onRefreshHistory={() => desktopApi.list_history().then(() => desktopApi.get_ui_state().then(setUiState))}
        onSaveAlerts={handleSaveAlerts}
        onClearAlerts={() => desktopApi.clear_alert_config({ form }).then(() => desktopApi.get_ui_state().then(setUiState))}
        onInstallBackgroundSchedule={handleInstallBackgroundSchedule}
        onUninstallBackgroundSchedule={handleUninstallBackgroundSchedule}
        onEnvironmentCheck={handleEnvironmentCheck}
      />
    </div>
  );
}

export default App;
