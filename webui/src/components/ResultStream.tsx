import type { Dispatch, SetStateAction } from "react";
import type { ResultRow, UIState } from "../types";
import { EmptyState, ToolbarButton } from "./common";
import { RawResults } from "./RawResults";
import {
  confidenceClass,
  confidenceLabel,
  formatMoney,
  numberValue,
} from "./resultUtils";

function FetchSummaryCard({ trust }: { trust: UIState["results"]["trust"] }) {
  const fetch = trust?.fetchQualityTelemetry ?? {};
  const parser = trust?.parserRecoveryTelemetry ?? {};
  const snapshot = trust?.snapshotSummary ?? {};
  const repair = trust?.repairPlan?.summary ?? {};
  const confirmation = trust?.priceConfirmationSummary ?? {};
  const total = numberValue(fetch.fetch_total_regions);
  if (!total) return null;
  const items = [
    ["最终命中", `${numberValue(fetch.fetch_price_found_count)} / ${total}`],
    ["OpenCLI 直接", numberValue(fetch.opencli_direct_price_found_count)],
    ["Fallback 救回", numberValue(fetch.fallback_rescued_count)],
    ["Challenge", numberValue(fetch.fetch_challenge_count)],
    ["Parse failed", numberValue(fetch.fetch_parse_failed_count)],
    ["Tabs", `${numberValue(fetch.tab_open_total)} opened / ${numberValue(fetch.tab_reuse_total)} reused`],
    ["Candidates", numberValue(parser.price_candidate_total)],
    ["Snapshots", numberValue(snapshot.snapshot_recommended_count)],
    ["Repair", numberValue(repair.total_repair_tasks)],
    ["Confirmed", `${numberValue(confirmation.confirmed)} / ${numberValue(confirmation.total)}`],
  ];
  return (
    <section className="trust-summary-panel">
      <div>
        <p className="eyebrow">Fetch Trust</p>
        <h3>抓取与解析质量</h3>
      </div>
      <div className="trust-metric-grid">
        {items.map(([label, value]) => (
          <div key={String(label)} className="trust-metric">
            <span>{label}</span>
            <strong>{String(value)}</strong>
          </div>
        ))}
      </div>
    </section>
  );
}

export function ResultStream({
  results,
  historyDetail,
  filteredResults,
  showDetails,
  detailTab,
  showRawResults,
  showSuccess,
  showFailure,
  showChangedOnly,
  showLowestOnly,
  sourceFilter,
  selectedTripLabel,
  onShowDetailsChange,
  onDetailTabChange,
  onShowRawResultsChange,
  onShowSuccessChange,
  onShowFailureChange,
  onShowChangedOnlyChange,
  onShowLowestOnlyChange,
  onSourceFilterChange,
  onSelectedTripLabelChange,
  onOpenLink,
  onConfirmPrice,
  onQueueFailure,
  onRepairAction,
}: {
  results: UIState["results"];
  historyDetail: string;
  filteredResults: { successRows: ResultRow[]; failureRows: ResultRow[] };
  showDetails: boolean;
  detailTab: "calendar" | "compare" | "history";
  showRawResults: boolean;
  showSuccess: boolean;
  showFailure: boolean;
  showChangedOnly: boolean;
  showLowestOnly: boolean;
  sourceFilter: "all" | "live" | "bookable";
  selectedTripLabel: string;
  onShowDetailsChange: Dispatch<SetStateAction<boolean>>;
  onDetailTabChange: Dispatch<SetStateAction<"calendar" | "compare" | "history">>;
  onShowRawResultsChange: Dispatch<SetStateAction<boolean>>;
  onShowSuccessChange: Dispatch<SetStateAction<boolean>>;
  onShowFailureChange: Dispatch<SetStateAction<boolean>>;
  onShowChangedOnlyChange: Dispatch<SetStateAction<boolean>>;
  onShowLowestOnlyChange: Dispatch<SetStateAction<boolean>>;
  onSourceFilterChange: Dispatch<SetStateAction<"all" | "live" | "bookable">>;
  onSelectedTripLabelChange: Dispatch<SetStateAction<string>>;
  onOpenLink: (url: string) => void;
  onConfirmPrice: (row: ResultRow, status: "confirmed" | "mismatched") => void;
  onQueueFailure: (row: ResultRow) => void;
  onRepairAction: (payload: Record<string, unknown>) => void;
}) {
  const cheapestCard = results.cheapestConclusion;
  const recommendationCard = results.recommendationConclusion;
  const filterSummaryParts: string[] = [];
  if (sourceFilter !== "all") filterSummaryParts.push(sourceFilter === "live" ? "仅实时" : "仅可下单");
  if (showLowestOnly) filterSummaryParts.push("仅最低价");
  if (showChangedOnly) filterSummaryParts.push("仅变化");
  if (selectedTripLabel) filterSummaryParts.push(selectedTripLabel);

  return (
    <div className="result-stream">
      <section className="summary-card">
        <p className="eyebrow">最低价结论</p>
        <h2>{String(cheapestCard.headline ?? "-")}</h2>
        <div className="summary-price">{String(cheapestCard.price ?? "-")}</div>
        <p className="summary-supporting">{String(cheapestCard.supporting ?? "")}</p>
        <p className="summary-meta">{String(cheapestCard.meta ?? "")}</p>
        <p className="summary-insight">{String(cheapestCard.insight ?? "")}</p>
        {cheapestCard.link ? (
          <button className="primary-button subtle" onClick={() => onOpenLink(String(cheapestCard.link))} type="button">
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
          <button className="primary-button subtle" onClick={() => onOpenLink(String(recommendationCard.link))} type="button">
            {String(recommendationCard.button_text ?? "打开链接")}
          </button>
        ) : null}
      </section>

      <FetchSummaryCard trust={results.trust} />

      {results.topRecommendations.length > 0 && (
        <div className="top-rec-panel">
          <div className="panel-label">Top 方案</div>
          <div className="top-rec-list">
            {results.topRecommendations.map((row, index) => (
              <button
                key={`${String(row.date)}-${String(row.region_code)}-${index}`}
                className="top-rec-item"
                onClick={() => row.link && onOpenLink(String(row.link))}
                type="button"
              >
                <span>{index + 1}</span>
                <div>
                  <strong>{String(row.region_name ?? "-")}</strong>
                  <small>{String(row.date ?? "-")} · {String(row.route ?? "-")}</small>
                </div>
                <em>{formatMoney(row.cheapest_cny_price)}</em>
                <span className={`trust-badge ${confidenceClass(row.confidence)}`}>
                  {confidenceLabel(row.confidence)}
                </span>
              </button>
            ))}
          </div>
        </div>
      )}

      {!showDetails ? (
        <button className="ghost-button wide-button" onClick={() => onShowDetailsChange(true)} type="button">
          查看详细结果
        </button>
      ) : (
        <div className="details-panel">
          <div className="toolbar">
            <ToolbarButton active={showRawResults} label={showRawResults ? "隐藏原始结果" : "显示原始结果"} onClick={() => onShowRawResultsChange((current) => !current)} />
            <ToolbarButton active={detailTab === "calendar"} label="日历" onClick={() => onDetailTabChange("calendar")} />
            <ToolbarButton active={detailTab === "compare"} label="对比" onClick={() => onDetailTabChange("compare")} />
            <ToolbarButton active={detailTab === "history"} label="历史" onClick={() => onDetailTabChange("history")} />
          </div>

          {showRawResults && (
            <RawResults
              filteredResults={filteredResults}
              filterSummaryParts={filterSummaryParts}
              selectedTripLabel={selectedTripLabel}
              sourceFilter={sourceFilter}
              showLowestOnly={showLowestOnly}
              showChangedOnly={showChangedOnly}
              showSuccess={showSuccess}
              showFailure={showFailure}
              trust={results.trust}
              onSelectedTripLabelChange={onSelectedTripLabelChange}
              onSourceFilterChange={onSourceFilterChange}
              onShowLowestOnlyChange={onShowLowestOnlyChange}
              onShowChangedOnlyChange={onShowChangedOnlyChange}
              onShowSuccessChange={onShowSuccessChange}
              onShowFailureChange={onShowFailureChange}
              onOpenLink={onOpenLink}
              onConfirmPrice={onConfirmPrice}
              onQueueFailure={onQueueFailure}
              onRepairAction={onRepairAction}
            />
          )}

          {detailTab === "calendar" && (
            <div className="calendar-panel">
              <p className="panel-note">{results.calendar.summaryText ?? "选择日期组合进行筛选。"}</p>
              {results.calendar.kind === "empty" ? (
                <EmptyState text="当前没有日历数据。" />
              ) : (
                <div className={`calendar-grid ${results.calendar.kind}`}>
                  {results.calendar.cells.map((cell) => (
                    <button
                      key={cell.tripLabel}
                      className={`calendar-cell ${selectedTripLabel === cell.tripLabel ? "active" : ""}`}
                      onClick={() => onSelectedTripLabelChange((current) => (current === cell.tripLabel ? "" : cell.tripLabel))}
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
              {results.compareRows.length ? (
                results.compareRows.map((row, index) => (
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
            <div className="history-panel">
              <pre className="history-detail">{historyDetail}</pre>
            </div>
          )}

          <button className="ghost-button wide-button" onClick={() => onShowDetailsChange(false)} type="button">
            收起详细结果
          </button>
        </div>
      )}
    </div>
  );
}
