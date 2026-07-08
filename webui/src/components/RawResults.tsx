import { Fragment, useState } from "react";
import type { Dispatch, SetStateAction } from "react";
import type { ResultRow, UIState } from "../types";
import { EmptyState, ToolbarButton } from "./common";
import {
  confidenceClass,
  confidenceLabel,
  fallbackAttemptLabel,
  formatMoney,
  listSummary,
  priceSourceLabel,
  warningsSummary,
} from "./resultUtils";

function DataTable({
  columns,
  rows,
  onOpenLink,
  highlightFailure,
  onQueueRetry,
  onConfirmPrice,
}: {
  columns: Array<{ key: string; label: string; align?: "right" | "left" }>;
  rows: ResultRow[];
  onOpenLink: (url: string) => void;
  highlightFailure?: boolean;
  onQueueRetry?: (row: ResultRow) => void;
  onConfirmPrice?: (row: ResultRow, status: "confirmed" | "mismatched") => void;
}) {
  const [expandedKey, setExpandedKey] = useState<string | null>(null);

  if (!rows.length) {
    return <EmptyState text={highlightFailure ? "当前没有失败市场。" : "当前没有可展示结果。"} />;
  }

  const rowKey = (row: ResultRow, index: number) =>
    `${String(row.date)}-${String(row.route)}-${String(row.region_code)}-${index}`;

  const hasDrillDown = (row: ResultRow) =>
    Boolean(
      (row.parser_warnings && row.parser_warnings.length > 0) ||
        row.evidence_text ||
        row.candidate_sources?.length ||
        row.fallback_attempts?.length,
    );

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
            const key = rowKey(row, index);
            const expanded = expandedKey === key;
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
              <Fragment key={key}>
                <tr className={rowClassName}>
                  {columns.map((column) => (
                    <td key={column.key} className={column.align === "right" ? "align-right" : ""}>
                      {column.key.includes("price") ? (
                        formatMoney(row[column.key])
                      ) : column.key === "confidence" ? (
                        <span className={`trust-badge ${confidenceClass(row.confidence)}`}>
                          {confidenceLabel(row.confidence)}
                        </span>
                      ) : column.key === "price_source" ? (
                        <span className="source-badge">{priceSourceLabel(row.price_source)}</span>
                      ) : column.key === "parser_warnings" ? (
                        <span className={warningsSummary(row.parser_warnings) === "-" ? "muted-cell" : "warning-cell"}>
                          {warningsSummary(row.parser_warnings)}
                        </span>
                      ) : (
                        String(row[column.key] ?? "-")
                      )}
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
                      {!highlightFailure && onConfirmPrice ? (
                        <>
                          <button className="toolbar-button" onClick={() => onConfirmPrice(row, "confirmed")} type="button">
                            确认
                          </button>
                          <button className="toolbar-button" onClick={() => onConfirmPrice(row, "mismatched")} type="button">
                            不符
                          </button>
                        </>
                      ) : null}
                      {hasDrillDown(row) ? (
                        <button
                          className="toolbar-button"
                          onClick={() => setExpandedKey(expanded ? null : key)}
                          type="button"
                        >
                          {expanded ? "收起" : "详情"}
                        </button>
                      ) : null}
                    </div>
                  </td>
                </tr>
                {expanded ? (
                  <tr className="warning-detail-row" key={`${key}-detail`}>
                    <td colSpan={columns.length + 1}>
                      <div className="warning-detail-panel">
                        {row.parser_warnings && row.parser_warnings.length > 0 ? (
                          <div className="warning-detail-section">
                            <strong>完整警告</strong>
                            <ul>
                              {row.parser_warnings.map((warning, widx) => (
                                <li key={widx}>{String(warning)}</li>
                              ))}
                            </ul>
                          </div>
                        ) : null}
                        {row.evidence_text ? (
                          <div className="warning-detail-section">
                            <strong>证据片段</strong>
                            <p>{String(row.evidence_text)}</p>
                          </div>
                        ) : null}
                        {row.candidate_sources && row.candidate_sources.length > 0 ? (
                          <div className="warning-detail-section">
                            <strong>候选来源</strong>
                            <ul>
                              {row.candidate_sources.map((source, sidx) => (
                                <li key={sidx}>{String(source)}</li>
                              ))}
                            </ul>
                          </div>
                        ) : null}
                        {row.fallback_attempts && row.fallback_attempts.length > 0 ? (
                          <div className="warning-detail-section">
                            <strong>Fallback chain</strong>
                            <p>{row.fallback_attempts.map((attempt) => fallbackAttemptLabel(attempt)).join(" -> ")}</p>
                          </div>
                        ) : null}
                        <div className="warning-detail-meta">
                          <span>Readiness: {String(row.readiness ?? "-")}</span>
                          <span>来源: {priceSourceLabel(row.price_source)}</span>
                          <span>候选数: {String(row.price_candidates_count ?? 0)}</span>
                          <span>选中位次: {String(row.selected_candidate_rank ?? "-")}</span>
                        </div>
                      </div>
                    </td>
                  </tr>
                ) : null}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function FailureReasonPanel({
  trust,
  onAction,
}: {
  trust: UIState["results"]["trust"];
  onAction: (payload: Record<string, unknown>) => void;
}) {
  const byClass = trust?.repairPlan?.summary?.by_failure_class;
  const tasks = trust?.repairPlan?.tasks ?? [];
  if (!tasks.length && (!byClass || typeof byClass !== "object")) return null;
  const entries = byClass && typeof byClass === "object" ? Object.entries(byClass) : [];
  const actionable = entries.filter(([, count]) => Number(count) > 0);
  const waitFailureClass = actionable.find(([failureClass]) => failureClass === "still_loading" || failureClass === "empty_shell")?.[0];
  return (
    <section className="trust-detail-panel">
      <h4>失败市场修复</h4>
      <div className="failure-chip-row">
        {entries.map(([reason, count]) => (
          <span key={reason} className="failure-chip">{reason}: {String(count)}</span>
        ))}
      </div>
      <div className="repair-action-row">
        {actionable.slice(0, 4).map(([failureClass]) => (
          <button
            className="toolbar-button"
            key={`queue-${failureClass}`}
            onClick={() => onAction({ action: "queue_retry", failureClass })}
            type="button"
          >
            加入 {failureClass}
          </button>
        ))}
        {waitFailureClass ? (
          <button className="toolbar-button" onClick={() => onAction({ action: "extend_wait", failureClass: waitFailureClass })} type="button">
            延长等待
          </button>
        ) : null}
        {actionable.some(([failureClass]) => failureClass === "challenge") ? (
          <button className="toolbar-button" onClick={() => onAction({ action: "open_links", failureClass: "challenge" })} type="button">
            打开验证链接
          </button>
        ) : null}
        {actionable.length ? (
          <button className="toolbar-button" onClick={() => onAction({ action: "skip" })} type="button">
            本轮跳过
          </button>
        ) : null}
      </div>
    </section>
  );
}

function ParserEvidencePanel({ rows }: { rows: ResultRow[] }) {
  const interesting = rows.filter((row) => row.evidence_text || row.price_candidates_count || row.fallback_attempts?.length).slice(0, 5);
  if (!interesting.length) return null;
  return (
    <section className="trust-detail-panel">
      <h4>解析证据</h4>
      <div className="evidence-list">
        {interesting.map((row, index) => (
          <div key={`${String(row.region_code)}-${index}`} className="evidence-item">
            <strong>{String(row.region_name ?? row.region_code ?? "-")}</strong>
            <span>来源 {priceSourceLabel(row.price_source)} · 候选 {String(row.price_candidates_count ?? 0)} · 选中 #{String(row.selected_candidate_rank ?? "-")}</span>
            <small>候选来源：{listSummary(row.candidate_sources)}</small>
            <small>Readiness：{String(row.readiness ?? "-")}</small>
            {row.evidence_text ? <p>{String(row.evidence_text)}</p> : null}
            {row.fallback_attempts?.length ? <small>Fallback chain: {row.fallback_attempts.map((attempt) => fallbackAttemptLabel(attempt)).join(" -> ")}</small> : null}
          </div>
        ))}
      </div>
    </section>
  );
}

export function RawResults({
  filteredResults,
  filterSummaryParts,
  selectedTripLabel,
  sourceFilter,
  showLowestOnly,
  showChangedOnly,
  showSuccess,
  showFailure,
  trust,
  onSelectedTripLabelChange,
  onSourceFilterChange,
  onShowLowestOnlyChange,
  onShowChangedOnlyChange,
  onShowSuccessChange,
  onShowFailureChange,
  onOpenLink,
  onConfirmPrice,
  onQueueFailure,
  onRepairAction,
}: {
  filteredResults: { successRows: ResultRow[]; failureRows: ResultRow[] };
  filterSummaryParts: string[];
  selectedTripLabel: string;
  sourceFilter: "all" | "live" | "bookable";
  showLowestOnly: boolean;
  showChangedOnly: boolean;
  showSuccess: boolean;
  showFailure: boolean;
  trust: UIState["results"]["trust"];
  onSelectedTripLabelChange: Dispatch<SetStateAction<string>>;
  onSourceFilterChange: Dispatch<SetStateAction<"all" | "live" | "bookable">>;
  onShowLowestOnlyChange: Dispatch<SetStateAction<boolean>>;
  onShowChangedOnlyChange: Dispatch<SetStateAction<boolean>>;
  onShowSuccessChange: Dispatch<SetStateAction<boolean>>;
  onShowFailureChange: Dispatch<SetStateAction<boolean>>;
  onOpenLink: (url: string) => void;
  onConfirmPrice: (row: ResultRow, status: "confirmed" | "mismatched") => void;
  onQueueFailure: (row: ResultRow) => void;
  onRepairAction: (payload: Record<string, unknown>) => void;
}) {
  return (
    <>
      <div className="filter-bar">
        <div className="filter-chips">
          <ToolbarButton active={!selectedTripLabel && sourceFilter === "all"} label="全部" onClick={() => { onSelectedTripLabelChange(""); onSourceFilterChange("all"); }} />
          <ToolbarButton active={sourceFilter === "live"} label="仅实时" onClick={() => onSourceFilterChange("live")} />
          <ToolbarButton active={sourceFilter === "bookable"} label="仅可下单" onClick={() => onSourceFilterChange("bookable")} />
          <ToolbarButton active={showLowestOnly} label="仅最低价" onClick={() => onShowLowestOnlyChange((current) => !current)} />
          <ToolbarButton active={showChangedOnly} label="仅变化" onClick={() => onShowChangedOnlyChange((current) => !current)} />
        </div>
        {filterSummaryParts.length > 0 && (
          <div className="filter-summary">
            <span>{filterSummaryParts.join(" · ")}</span>
            <button
              className="text-button"
              onClick={() => {
                onSelectedTripLabelChange("");
                onSourceFilterChange("all");
                onShowLowestOnlyChange(false);
                onShowChangedOnlyChange(false);
              }}
              type="button"
            >
              清除
            </button>
          </div>
        )}
        <div className="toggle-row">
          <label><input checked={showSuccess} onChange={(e) => onShowSuccessChange(e.target.checked)} type="checkbox" /> 成功</label>
          <label><input checked={showFailure} onChange={(e) => onShowFailureChange(e.target.checked)} type="checkbox" /> 失败</label>
        </div>
      </div>

      <div className="table-section">
        <h4>成功结果 <small>{filteredResults.successRows.length}</small></h4>
        <ParserEvidencePanel rows={filteredResults.successRows} />
        <DataTable
          columns={[
            { key: "date", label: "日期" },
            { key: "route", label: "航段" },
            { key: "region_name", label: "地区" },
            { key: "source_label", label: "来源" },
            { key: "best_cny_price", label: "最佳价", align: "right" },
            { key: "cheapest_cny_price", label: "最低价", align: "right" },
            { key: "confidence", label: "可信度" },
            { key: "price_source", label: "价格来源" },
            { key: "parser_warnings", label: "警告" },
            { key: "delta_label", label: "变化" },
          ]}
          rows={filteredResults.successRows}
          onOpenLink={onOpenLink}
          onConfirmPrice={onConfirmPrice}
        />
      </div>

      <div className="table-section">
        <h4>
          失败市场 <small>{filteredResults.failureRows.length}</small>
          <button
            className="text-button"
            onClick={() => onRepairAction({ action: "run_retry" })}
            type="button"
          >
            运行补扫队列
          </button>
        </h4>
        <FailureReasonPanel trust={trust} onAction={onRepairAction} />
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
          onOpenLink={onOpenLink}
          highlightFailure
          onQueueRetry={onQueueFailure}
        />
      </div>
    </>
  );
}
