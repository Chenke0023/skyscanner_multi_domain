import type { Dispatch, ReactNode, RefObject, SetStateAction } from "react";
import type { FormState } from "../types";

export type SuggestionMap = {
  origin: Array<{ name: string; code: string; kind: string; label: string }>;
  destination: Array<{ name: string; code: string; kind: string; label: string }>;
};

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
  return (
    <div className="date-field">
      <label className="field-label">{label}</label>
      <div className="date-input-shell">
        <input
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
        className="flex items-center gap-1 text-sm text-stone-500 hover:text-stone-800 transition-colors cursor-pointer"
      >
        {label}
      </button>
      <div
        className={`grid transition-all duration-300 ease-out ${open ? "grid-rows-[1fr] opacity-100 mt-6 pt-6 border-t border-stone-100" : "grid-rows-[0fr] opacity-0 mt-0 pt-0 border-t border-transparent"}`}
      >
        <div className="overflow-hidden">{open ? children : null}</div>
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
    <div className="query-card">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-6">
        <div className="relative" ref={originRef}>
          <label className="field-label">出发地</label>
          <input
            className="form-control"
            value={form.origin}
            onChange={(e) => onFormPatch({ origin: e.target.value })}
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
                    onFormPatch({ origin: item.name });
                    onSuggestionsChange((current) => ({ ...current, origin: [] }));
                  }}
                >
                  {item.label}
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="relative" ref={destRef}>
          <label className="field-label">目的地</label>
          <input
            className="form-control"
            value={form.destination}
            onChange={(e) => onFormPatch({ destination: e.target.value })}
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
                    onFormPatch({ destination: item.name });
                    onSuggestionsChange((current) => ({ ...current, destination: [] }));
                  }}
                >
                  {item.label}
                </button>
              ))}
            </div>
          )}
        </div>

        <div>
          <label className="field-label">行程</label>
          <select
            className="form-control cursor-pointer"
            value={form.trip_type}
            onChange={(e) => onFormPatch({ trip_type: e.target.value })}
          >
            <option value="one_way">单程</option>
            <option value="round_trip">往返</option>
          </select>
        </div>

        <DateField label="出发日期" value={form.date} onChange={(value) => onFormPatch({ date: value })} />

        {form.trip_type === "round_trip" ? (
          <div className="sm:col-span-2">
            <DateField
              label="返程日期"
              value={form.return_date}
              min={form.date}
              onChange={(value) => onFormPatch({ return_date: value })}
            />
          </div>
        ) : null}
      </div>

      <div className="mt-6">
        <Collapsible
          open={advancedOpen}
          onToggle={() => onAdvancedOpenChange((current) => !current)}
          label="高级搜索设置"
        >
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-6">
            <div>
              <label className="field-label">等待秒数</label>
              <input
                className="form-control"
                value={form.wait}
                onChange={(e) => onFormPatch({ wait: e.target.value })}
              />
            </div>
            <div>
              <label className="field-label">±天数</label>
              <input
                className="form-control"
                value={form.date_window}
                onChange={(e) => onFormPatch({ date_window: e.target.value })}
              />
            </div>
            <div className="sm:col-span-2">
              <label className="field-label">额外地区</label>
              <input
                className="form-control"
                value={form.regions}
                onChange={(e) => onFormPatch({ regions: e.target.value })}
              />
              <p className="text-xs text-stone-400 mt-1.5">{hintsRegions}</p>
            </div>
            <div className="sm:col-span-2 advanced-switch-grid">
              <Switch checked={form.combined_summary} onChange={(value) => onFormPatch({ combined_summary: value })} label="保存汇总" />
              <Switch checked={form.exact_airport} onChange={(value) => onFormPatch({ exact_airport: value })} label="严格机场" />
              <Switch checked={form.origin_country} onChange={(value) => onFormPatch({ origin_country: value })} label="出发按国家" />
              <Switch checked={form.destination_country} onChange={(value) => onFormPatch({ destination_country: value })} label="目的按国家" />
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
    </div>
  );
}
