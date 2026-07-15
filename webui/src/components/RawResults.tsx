import { Fragment, useState } from "react";
import type { Dispatch, SetStateAction } from "react";
import type { ResultRow, UIState } from "../types";
import { EmptyState, ToolbarButton } from "./common";
import { formatDuration, normalizeItineraryLegs } from "./itinerary";
import {
  confidenceClass,
  confidenceLabel,
  formatMoney,
  priceSourceLabel,
  warningsSummary,
} from "./resultUtils";

function visibleText(value: unknown): string {
  const text = String(value ?? "").trim();
  return text && text !== "-" ? text : "";
}

function technicalDetailEntries(row: ResultRow): Array<[string, string]> {
  const entries: Array<[string, string]> = [
    ["原始状态码", visibleText(row.status)],
    ["原始错误", visibleText(row.error)],
    ["Readiness", visibleText(row.readiness)],
  ];
  return entries.filter(([, value]) => Boolean(value));
}

function ItineraryCell({ row }: { row: ResultRow }) {
  const legs = normalizeItineraryLegs(row.itinerary_legs);
  const visibleLegs = legs.map((leg, index) => {
    const details: string[] = [];
    if (leg.departure_time && leg.arrival_time) details.push(`${leg.departure_time}–${leg.arrival_time}`);
    if (Number.isInteger(leg.stop_count)) {
      details.push(leg.stop_count === 0 ? "直飞" : `经停${leg.stop_count}次`);
    }
    const duration = formatDuration(leg.duration_minutes);
    if (duration) details.push(duration);
    return {
      label: leg.direction === "return" || index === 1 ? "返程" : "去程",
      details,
    };
  }).filter((leg) => leg.details.length > 0);

  if (!visibleLegs.length) return <span className="muted-cell">-</span>;
  return (
    <div className="itinerary-cell">
      {visibleLegs.map((leg) => (
        <div key={leg.label}><strong>{leg.label}</strong> {leg.details.join(" · ")}</div>
      ))}
    </div>
  );
}

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
        technicalDetailEntries(row).length,
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
                  {columns.map((column) => {
                    const cellClassName = [
                      column.align === "right" ? "align-right" : "",
                      column.key === "error" ? "error-cell" : "",
                    ].filter(Boolean).join(" ");
                    return (
                      <td key={column.key} className={cellClassName}>
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
                        ) : column.key === "itinerary_legs" ? (
                          <ItineraryCell row={row} />
                        ) : column.key === "error" ? (
                          <span className={visibleText(row.error) ? "error-cell-text" : "muted-cell"}>
                            {visibleText(row.error) || "-"}
                          </span>
                        ) : (
                          String(row[column.key] ?? "-")
                        )}
                      </td>
                    );
                  })}
                  <td>
                    <div className="row-actions">
                      {typeof row.link === "string" && row.link.startsWith("http") ? (
                        <button className="toolbar-button" onClick={() => onOpenLink(String(row.link))} type="button">
                          打开页面
                        </button>
                      ) : null}
                      {highlightFailure && onQueueRetry ? (
                        <button className="toolbar-button" onClick={() => onQueueRetry(row)} type="button">
                          重新扫描
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
                          {expanded ? "收起技术详情" : "技术详情"}
                        </button>
                      ) : null}
                    </div>
                  </td>
                </tr>
                {expanded ? (
                  <tr className="warning-detail-row" key={`${key}-detail`}>
                    <td colSpan={columns.length + 1}>
                      <div className="warning-detail-panel">
                        {technicalDetailEntries(row).length > 0 ? (
                          <div className="warning-detail-section">
                            <strong>技术详情</strong>
                            <dl className="failure-detail-list">
                              {technicalDetailEntries(row).map(([label, value]) => (
                                <Fragment key={label}>
                                  <dt>{label}</dt>
                                  <dd>{value}</dd>
                                </Fragment>
                              ))}
                            </dl>
                          </div>
                        ) : null}
                        {row.parser_warnings && row.parser_warnings.length > 0 ? (
                          <div className="warning-detail-section">
                            <strong>解析警告</strong>
                            <ul>
                              {row.parser_warnings.map((warning, widx) => (
                                <li key={widx}>{String(warning)}</li>
                              ))}
                            </ul>
                          </div>
                        ) : null}
                        {row.evidence_text ? (
                          <div className="warning-detail-section">
                            <strong>采集证据</strong>
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

function failureClassLabel(value: string): string {
  if (["challenge", "captcha"].includes(value)) return "需要人工验证";
  if (["still_loading", "empty_shell", "timeout"].includes(value)) return "页面未加载完成";
  if (["no_flights", "no_results"].includes(value)) return "未找到可用航班";
  if (["parse_failed", "unpriced"].includes(value)) return "未能读取价格";
  if (["browser_missing", "browser_unavailable"].includes(value)) return "浏览器不可用";
  if (["network", "connection"].includes(value)) return "连接失败";
  return "需要重新扫描";
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
      <h4>问题汇总</h4>
      <div className="failure-chip-row">
        {entries.map(([reason, count]) => (
          <span key={reason} className="failure-chip">{failureClassLabel(reason)}: {String(count)}</span>
        ))}
      </div>
      <div className="repair-action-row">
        {actionable.length ? (
          <button className="toolbar-button" onClick={() => onAction({ action: "run_retry" })} type="button">
            重新扫描失败项
          </button>
        ) : null}
        {waitFailureClass ? (
          <button className="toolbar-button" onClick={() => onAction({ action: "extend_wait", failureClass: waitFailureClass })} type="button">
            延长页面等待
          </button>
        ) : null}
        {actionable.some(([failureClass]) => failureClass === "challenge") ? (
          <button className="toolbar-button" onClick={() => onAction({ action: "open_links", failureClass: "challenge" })} type="button">
            打开验证页面
          </button>
        ) : null}
        {actionable.length ? (
          <button className="toolbar-button" onClick={() => onAction({ action: "skip" })} type="button">
            暂不处理
          </button>
        ) : null}
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
        <DataTable
          columns={[
            { key: "date", label: "日期" },
            { key: "route", label: "航段" },
            { key: "region_name", label: "地区" },
            { key: "source_label", label: "来源" },
            { key: "best_cny_price", label: "最佳价", align: "right" },
            { key: "cheapest_cny_price", label: "最低价", align: "right" },
            { key: "itinerary_legs", label: "最低价行程" },
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
          未完成结果 <small>{filteredResults.failureRows.length}</small>
          <button
            className="text-button"
            onClick={() => onRepairAction({ action: "run_retry" })}
            type="button"
          >
            重新扫描失败项
          </button>
        </h4>
        <FailureReasonPanel trust={trust} onAction={onRepairAction} />
        <DataTable
          columns={[
            { key: "date", label: "日期" },
            { key: "route", label: "航段" },
            { key: "region_name", label: "地区" },
            { key: "failure_category", label: "遇到的问题" },
            { key: "failure_action", label: "处理方式" },
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
