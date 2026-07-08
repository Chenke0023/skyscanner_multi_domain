import type { Dispatch, SetStateAction } from "react";
import type { HistoryRecord, UIState } from "../types";
import { EmptyState } from "./common";
import { Switch } from "./QueryCard";

export type AlertDraft = {
  targetPrice: string;
  dropAmount: string;
  autoRefreshMinutes: string;
  autoRefreshMode: "app" | "background";
  notificationsEnabled: boolean;
  notifyOnRecovery: boolean;
  notifyOnNewLow: boolean;
};

export const defaultAlertDraft: AlertDraft = {
  targetPrice: "",
  dropAmount: "",
  autoRefreshMinutes: "",
  autoRefreshMode: "app",
  notificationsEnabled: true,
  notifyOnRecovery: true,
  notifyOnNewLow: true,
};

export type BackgroundScheduleDraft = {
  intervalMinutes: string;
  onlyOnAcPower: boolean;
};

export const defaultBackgroundScheduleDraft: BackgroundScheduleDraft = {
  intervalMinutes: "600",
  onlyOnAcPower: true,
};

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

export function Drawers({
  leftOpen,
  rightOpen,
  uiState,
  alertDraft,
  backgroundScheduleDraft,
  onCloseLeft,
  onCloseRight,
  onAlertDraftChange,
  onBackgroundScheduleDraftChange,
  onApplyHistory,
  onOpenOutputs,
  onExportDecisionSummary,
  onToggleFavorite,
  onRefreshHistory,
  onSaveAlerts,
  onClearAlerts,
  onInstallBackgroundSchedule,
  onUninstallBackgroundSchedule,
  onEnvironmentCheck,
}: {
  leftOpen: boolean;
  rightOpen: boolean;
  uiState: UIState;
  alertDraft: AlertDraft;
  backgroundScheduleDraft: BackgroundScheduleDraft;
  onCloseLeft: () => void;
  onCloseRight: () => void;
  onAlertDraftChange: Dispatch<SetStateAction<AlertDraft>>;
  onBackgroundScheduleDraftChange: Dispatch<SetStateAction<BackgroundScheduleDraft>>;
  onApplyHistory: (record: HistoryRecord) => void;
  onOpenOutputs: () => void;
  onExportDecisionSummary: () => void;
  onToggleFavorite: () => void;
  onRefreshHistory: () => void;
  onSaveAlerts: () => void;
  onClearAlerts: () => void;
  onInstallBackgroundSchedule: () => void;
  onUninstallBackgroundSchedule: () => void;
  onEnvironmentCheck: () => void;
}) {
  return (
    <>
      {leftOpen && (
        <div className="drawer-overlay" onClick={onCloseLeft}>
          <aside className="drawer drawer-left" onClick={(event) => event.stopPropagation()}>
            <div className="drawer-header">
              <h3>历史与收藏</h3>
              <button className="toolbar-button" onClick={onCloseLeft} type="button">关闭</button>
            </div>
            <div className="drawer-body">
              <HistoryList title="收藏路线" records={uiState.history.favorites} onApply={onApplyHistory} />
              <HistoryList title="最近查询" records={uiState.history.recent} onApply={onApplyHistory} />
              <div className="drawer-actions">
                <button className="toolbar-button" onClick={onOpenOutputs} type="button">打开结果目录</button>
                <button className="toolbar-button" onClick={onExportDecisionSummary} type="button">导出决策摘要</button>
                <button className="toolbar-button" onClick={onToggleFavorite} type="button">收藏当前查询</button>
                <button className="toolbar-button" onClick={onRefreshHistory} type="button">刷新历史</button>
              </div>
            </div>
          </aside>
        </div>
      )}

      {rightOpen && (
        <div className="drawer-overlay" onClick={onCloseRight}>
          <aside className="drawer drawer-right" onClick={(event) => event.stopPropagation()}>
            <div className="drawer-header">
              <h3>设置</h3>
              <button className="toolbar-button" onClick={onCloseRight} type="button">关闭</button>
            </div>
            <div className="drawer-body">
              <div className="drawer-section">
                <h4>提醒与自动复扫</h4>
                <p className="drawer-hint">{uiState.alerts.summary}</p>
                <div className="grid gap-3">
                  <label className="field-label">目标价 ≤</label>
                  <input className="form-control" value={alertDraft.targetPrice} onChange={(event) => onAlertDraftChange((current) => ({ ...current, targetPrice: event.target.value }))} />
                  <label className="field-label">再降 ≥</label>
                  <input className="form-control" value={alertDraft.dropAmount} onChange={(event) => onAlertDraftChange((current) => ({ ...current, dropAmount: event.target.value }))} />
                  <label className="field-label">自动复扫(分钟)</label>
                  <input className="form-control" value={alertDraft.autoRefreshMinutes} onChange={(event) => onAlertDraftChange((current) => ({ ...current, autoRefreshMinutes: event.target.value }))} />
                  <label className="field-label">复扫模式</label>
                  <select
                    className="form-control"
                    value={alertDraft.autoRefreshMode}
                    onChange={(event) => onAlertDraftChange((current) => ({ ...current, autoRefreshMode: event.target.value === "background" ? "background" : "app" }))}
                  >
                    <option value="app">应用内</option>
                    <option value="background">后台</option>
                  </select>
                </div>
                <div className="mt-4 flex flex-col gap-3">
                  <Switch checked={alertDraft.notificationsEnabled} onChange={(value) => onAlertDraftChange((current) => ({ ...current, notificationsEnabled: value }))} label="启用桌面通知" />
                  <Switch checked={alertDraft.notifyOnRecovery} onChange={(value) => onAlertDraftChange((current) => ({ ...current, notifyOnRecovery: value }))} label="通知失败恢复" />
                  <Switch checked={alertDraft.notifyOnNewLow} onChange={(value) => onAlertDraftChange((current) => ({ ...current, notifyOnNewLow: value }))} label="通知刷新历史新低" />
                </div>
                <div className="drawer-actions">
                  <button className="toolbar-button active" onClick={onSaveAlerts} type="button">保存设置</button>
                  <button className="toolbar-button" onClick={onClearAlerts} type="button">清除</button>
                </div>
              </div>

              <div className="drawer-section">
                <h4>后台调度</h4>
                <p className="drawer-hint">后台模式路线会由 macOS 定时检查，到期后才执行复扫。</p>
                <div className="grid gap-3">
                  <label className="field-label">调度间隔(分钟)</label>
                  <input
                    className="form-control"
                    value={backgroundScheduleDraft.intervalMinutes}
                    onChange={(event) => onBackgroundScheduleDraftChange((current) => ({ ...current, intervalMinutes: event.target.value }))}
                  />
                  <Switch
                    checked={backgroundScheduleDraft.onlyOnAcPower}
                    onChange={(value) => onBackgroundScheduleDraftChange((current) => ({ ...current, onlyOnAcPower: value }))}
                    label="仅接电时运行"
                  />
                </div>
                <div className="drawer-actions">
                  <button className="toolbar-button active" onClick={onInstallBackgroundSchedule} type="button">安装/更新后台调度</button>
                  <button className="toolbar-button" onClick={onUninstallBackgroundSchedule} type="button">卸载后台调度</button>
                </div>
              </div>

              <div className="drawer-section">
                <h4>环境状态</h4>
                <button className="toolbar-button" onClick={onEnvironmentCheck} type="button">检查环境</button>
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
    </>
  );
}
