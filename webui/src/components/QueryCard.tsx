import type { Dispatch, ReactNode, RefObject, SetStateAction } from "react";
import type { FormState } from "../types";

export type SuggestionMap = {
  origin: Array<{ name: string; code: string; kind: string; label: string }>;
  destination: Array<{ name: string; code: string; kind: string; label: string }>;
};

function DateField({
  id,
  label,
  value,
  onChange,
  min,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  min?: string;
}) {
  return (
    <div className="date-field query-field query-field-date">
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      <div className="date-input-shell">
        <input
          id={id}
          aria-label={label}
          className="form-control"
          type="date"
          value={value}
          min={min}
          onChange={(e) => onChange(e.target.value)}
        />
      </div>
    </div>
  );
}

export function Switch({
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
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className="switch-control group flex items-center gap-3 cursor-pointer select-none"
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
  label,
  children,
}: {
  open: boolean;
  onToggle: () => void;
  label: string;
  children: ReactNode;
}) {
  return (
    <div>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="advanced-toggle"
      >
        <span>{label}</span>
        <span className="advanced-chevron" aria-hidden="true" />
      </button>
      <div
        aria-hidden={!open}
        className={`collapsible-content ${open ? "is-open" : ""}`}
      >
        <div className="collapsible-content-inner">{open ? children : null}</div>
      </div>
    </div>
  );
}

export function QueryCard({
  form,
  hintsRegions,
  suggestions,
  activeField,
  originRef,
  destRef,
  advancedOpen,
  busy,
  isPending,
  onActiveFieldChange,
  onFormPatch,
  onSuggestionsChange,
  onAdvancedOpenChange,
  onStartScan,
}: {
  form: FormState;
  hintsRegions: string;
  suggestions: SuggestionMap;
  activeField: "origin" | "destination" | null;
  originRef: RefObject<HTMLDivElement>;
  destRef: RefObject<HTMLDivElement>;
  advancedOpen: boolean;
  busy: boolean;
  isPending: boolean;
  onActiveFieldChange: (field: "origin" | "destination" | null) => void;
  onFormPatch: (patch: Partial<FormState>) => void;
  onSuggestionsChange: Dispatch<SetStateAction<SuggestionMap>>;
  onAdvancedOpenChange: Dispatch<SetStateAction<boolean>>;
  onStartScan: () => void;
}) {
  return (
    <section className="query-card" aria-label="航班搜索">
      <div className="query-card-heading">
        <div>
          <p className="query-kicker">航班搜索</p>
          <h1 id="query-card-title">比较不同市场的机票价格</h1>
        </div>
        <p>选择航线与日期，系统将核验每个站点的实际搜索路线。</p>
      </div>
      <div className="query-fields-grid">
        <div className="relative query-field query-field-origin" ref={originRef}>
          <div className="location-label-row">
            <label className="field-label" htmlFor="origin-input">出发地</label>
            <span className="location-mode-badge">
              {form.origin_country ? "国家范围" : form.exact_airport ? "机场" : "城市 / 机场 / 国家"}
            </span>
          </div>
          <input
            id="origin-input"
            className="form-control"
            value={form.origin}
            onChange={(e) => onFormPatch({ origin: e.target.value, origin_country: false })}
            onFocus={() => onActiveFieldChange("origin")}
            placeholder="例如：北京"
          />
          {activeField === "origin" && suggestions.origin.length > 0 && (
            <div className="suggestion-list">
              {suggestions.origin.map((item) => (
                <button
                  key={`${item.code}-${item.name}`}
                  type="button"
                  onClick={() => {
                    onFormPatch({ origin: item.name, origin_country: item.kind === "country" });
                    onActiveFieldChange(null);
                    onSuggestionsChange((current) => ({ ...current, origin: [] }));
                  }}
                >
                  <span>{item.label}</span>
                  <span className={`suggestion-kind suggestion-kind-${item.kind}`}>
                    {item.kind === "country" ? "国家" : item.kind === "metro" ? "城市" : "机场"}
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="relative query-field query-field-destination" ref={destRef}>
          <div className="location-label-row">
            <label className="field-label" htmlFor="destination-input">目的地</label>
            <span className="location-mode-badge">
              {form.destination_country ? "国家范围" : form.exact_airport ? "机场" : "城市 / 机场 / 国家"}
            </span>
          </div>
          <input
            id="destination-input"
            className="form-control"
            value={form.destination}
            onChange={(e) => onFormPatch({ destination: e.target.value, destination_country: false })}
            onFocus={() => onActiveFieldChange("destination")}
            placeholder="例如：东京"
          />
          {activeField === "destination" && suggestions.destination.length > 0 && (
            <div className="suggestion-list">
              {suggestions.destination.map((item) => (
                <button
                  key={`${item.code}-${item.name}`}
                  type="button"
                  onClick={() => {
                    onFormPatch({ destination: item.name, destination_country: item.kind === "country" });
                    onActiveFieldChange(null);
                    onSuggestionsChange((current) => ({ ...current, destination: [] }));
                  }}
                >
                  <span>{item.label}</span>
                  <span className={`suggestion-kind suggestion-kind-${item.kind}`}>
                    {item.kind === "country" ? "国家" : item.kind === "metro" ? "城市" : "机场"}
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="query-field query-field-trip">
          <label className="field-label" htmlFor="trip-type-select">行程</label>
          <select
            id="trip-type-select"
            className="form-control cursor-pointer"
            value={form.trip_type}
            onChange={(e) => onFormPatch({ trip_type: e.target.value })}
          >
            <option value="one_way">单程</option>
            <option value="round_trip">往返</option>
          </select>
        </div>

        <DateField
          id="departure-date-input"
          label="出发日期"
          value={form.date}
          onChange={(value) => onFormPatch({ date: value })}
        />

        {form.trip_type === "round_trip" ? (
          <div className="query-field query-field-return">
            <DateField
              id="return-date-input"
              label="返程日期"
              value={form.return_date}
              min={form.date}
              onChange={(value) => onFormPatch({ return_date: value })}
            />
          </div>
        ) : null}
      </div>

      <div className="advanced-section">
        <Collapsible
          open={advancedOpen}
          onToggle={() => onAdvancedOpenChange((current) => !current)}
          label="高级搜索设置"
        >
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-6">
            <div>
              <label className="field-label" htmlFor="wait-seconds-input">
                等待秒数
              </label>
              <input
                id="wait-seconds-input"
                className="form-control"
                value={form.wait}
                onChange={(e) => onFormPatch({ wait: e.target.value })}
              />
            </div>
            <div>
              <label className="field-label" htmlFor="date-window-input">
                ±天数
              </label>
              <input
                id="date-window-input"
                className="form-control"
                value={form.date_window}
                onChange={(e) => onFormPatch({ date_window: e.target.value })}
              />
            </div>
            <div className="sm:col-span-2">
              <label className="field-label" htmlFor="extra-regions-input">
                额外地区
              </label>
              <input
                id="extra-regions-input"
                className="form-control"
                value={form.regions}
                onChange={(e) => onFormPatch({ regions: e.target.value })}
              />
              <p className="text-xs text-stone-400 mt-1.5">{hintsRegions}</p>
            </div>
            <div className="sm:col-span-2 advanced-switch-grid">
              <Switch checked={form.combined_summary} onChange={(value) => onFormPatch({ combined_summary: value })} label="保存汇总" />
              <Switch checked={form.exact_airport} onChange={(value) => onFormPatch({ exact_airport: value })} label="严格机场" />
            </div>
          </div>
        </Collapsible>
      </div>

      <div className="query-card-actions">
        <button
          className="scan-button"
          disabled={busy || isPending}
          onClick={onStartScan}
          type="button"
        >
          {busy ? "扫描中..." : "开始比价"}
        </button>
      </div>
    </section>
  );
}
