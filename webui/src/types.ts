export type ProgressState = {
  step: number;
  total: number;
  date: string;
  regionName: string;
};

export type FormState = {
  origin: string;
  destination: string;
  trip_type: string;
  date: string;
  return_date: string;
  regions: string;
  wait: string;
  date_window: string;
  exact_airport: boolean;
  origin_country: boolean;
  destination_country: boolean;
  combined_summary: boolean;
};

export type HistoryRecord = {
  id: number | null;
  queryKey: string;
  title: string;
  createdAt: string;
  isFavorite: boolean;
  label: string;
  queryPayload: Record<string, unknown>;
};

export type AlertConfig = {
  query_key: string;
  title: string;
  query_payload: Record<string, unknown>;
  notifications_enabled: boolean;
  target_price: number | null;
  drop_amount: number | null;
  auto_refresh_minutes: number | null;
  notify_on_recovery: boolean;
  notify_on_new_low: boolean;
  last_notified_price: number | null;
  last_notified_at: string | null;
  last_auto_refresh_at: string | null;
};

export type ResultRow = Record<string, unknown> & {
  date?: string;
  route?: string;
  region_name?: string;
  region_code?: string;
  link?: string;
  cheapest_cny_price?: number | null;
  best_cny_price?: number | null;
  delta_label?: string;
  isCheapestHighlight?: boolean;
  isChangedHighlight?: boolean;
  isReuseReady?: boolean;
};

export type CalendarPayload = {
  kind: "empty" | "one_way" | "round_trip";
  summaryText?: string;
  departures?: string[];
  returnDates?: string[];
  cells: Array<{
    tripLabel: string;
    departure: string;
    returnDate?: string;
    price?: number | null;
    regionName?: string | null;
  }>;
};

export type UIState = {
  form: FormState;
  hints: {
    origin: string;
    destination: string;
    regions: string;
    effectiveRegions: string[];
  };
  status: {
    message: string;
    busy: boolean;
    error: string | null;
    progress: ProgressState;
  };
  environment: {
    lines: string[];
  };
  logs: Array<{ timestamp: string; message: string }>;
  history: {
    favorites: HistoryRecord[];
    recent: HistoryRecord[];
    historyDetail: string;
  };
  alerts: {
    config: AlertConfig | null;
    summary: string;
    pendingRetryRegions: string[];
  };
  results: {
    cheapestConclusion: Record<string, unknown>;
    recommendationConclusion: Record<string, unknown>;
    topRecommendations: ResultRow[];
    calendar: CalendarPayload;
    compareRows: Array<Record<string, string>>;
    successRows: ResultRow[];
    failureRows: ResultRow[];
    displayRows: ResultRow[];
    rowsByDate: Array<[string, ResultRow[]]>;
    quoteSnapshotsByDate: Array<[string, Record<string, unknown>[]]>;
  };
  outputs: {
    currentOutput: string | null;
    reportsDir: string;
  };
};

export type SuggestionResponse = {
  field: "origin" | "destination";
  items: Array<{
    name: string;
    code: string;
    kind: string;
    label: string;
  }>;
};
