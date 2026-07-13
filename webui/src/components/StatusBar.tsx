import type { UIState } from "../types";

export function StatusBar({
  status,
  environmentLines,
  actionMessage,
  progressText,
  onOpenSettings,
  onCancel,
}: {
  status: UIState["status"];
  environmentLines: string[];
  actionMessage: string;
  progressText: string;
  onOpenSettings: () => void;
  onCancel: () => void;
}) {
  const environmentStatus = status.error
    ? "error"
    : environmentLines.length > 0
      ? "ok"
      : "unknown";
  const environmentStatusLabel =
    environmentStatus === "error"
      ? `环境状态: ${status.error}`
      : environmentStatus === "ok"
        ? "环境状态: 已检查"
        : "环境状态: 未检查";
  const errorText = String(status.error ?? "").trim();
  const baseMessage = String(status.message ?? "").trim() || "就绪";
  const visibleMessage =
    errorText && !baseMessage.includes(errorText)
      ? `${baseMessage}: ${errorText}`
      : baseMessage;

  return (
    <footer className="status-bar">
      <div className="status-main flex items-center gap-2.5">
        <button
          aria-label={environmentStatusLabel}
          className={`status-dot ${environmentStatus}`}
          onClick={onOpenSettings}
          title={environmentStatusLabel}
          type="button"
        />
        <span className="status-message text-sm text-stone-500" title={visibleMessage}>
          {visibleMessage}
        </span>
        {status.progress.total > 0 ? (
          <span className="text-xs text-stone-400">
            {status.progress.step}/{status.progress.total}
          </span>
        ) : null}
        {progressText ? <span className="plan-progress-chip">{progressText}</span> : null}
      </div>
      <div className="flex items-center gap-2">
        {actionMessage ? <span className="text-sm text-stone-500">{actionMessage}</span> : null}
        {status.busy ? (
          <button
            className="topbar-button compact"
            onClick={onCancel}
            type="button"
          >
            取消
          </button>
        ) : null}
      </div>
    </footer>
  );
}
