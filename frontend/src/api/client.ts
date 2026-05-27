/**
 * Thin fetch wrapper. All routes go through Next's /api/* rewrite to the
 * backend (see next.config.js), so we just call them with the relative path.
 *
 * Auth is by `pf_session` cookie. Browsers send same-origin cookies
 * automatically, so we don't need to do anything special — but we DO need
 * `credentials: "same-origin"` (the default for fetch) and to NOT set any
 * forbidden cookie header explicitly.
 */

export class ApiError extends Error {
  constructor(public status: number, message: string, public body?: unknown) {
    super(message);
  }
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  opts: { redirectOn401?: boolean } = {},
): Promise<T> {
  const { redirectOn401 = true } = opts;
  const headers = new Headers(init.headers);
  if (!headers.has("Content-Type") && init.body) {
    headers.set("Content-Type", "application/json");
  }
  const res = await fetch(path, { ...init, headers, credentials: "same-origin" });
  if (res.status === 401) {
    // Session expired or invalid. The default is to kick to /login —
    // but `me()` opts out (it's the probe Nav uses to detect auth state).
    if (redirectOn401 && typeof window !== "undefined" && window.location.pathname !== "/login") {
      window.location.href = `/login?next=${encodeURIComponent(window.location.pathname)}`;
    }
    throw new ApiError(401, "not authenticated");
  }
  if (!res.ok) {
    // Read the body ONCE as text, then try to parse as JSON. The previous
    // code did res.json() then res.text() on failure, but res.json() drains
    // the body stream even when it throws, so the fallback failed with
    // "body stream already read". Read text up front, JSON-parse opportunistically.
    const text = await res.text();
    let body: unknown = text;
    try {
      body = JSON.parse(text);
    } catch {
      // not JSON — keep text
    }
    const detail =
      body && typeof body === "object" && "detail" in body
        ? String((body as { detail: unknown }).detail)
        : text || res.statusText;
    throw new ApiError(res.status, `${res.status}: ${detail}`, body);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  // Auth
  requestMagicLink: (email: string) =>
    request<void>(
      "/api/auth/request",
      { method: "POST", body: JSON.stringify({ email }) },
      { redirectOn401: false },
    ),
  me: () => request<Me>("/api/auth/me", {}, { redirectOn401: false }),
  logout: () =>
    request<void>("/api/auth/logout", { method: "POST" }, { redirectOn401: false }),
  // Users
  createUser: (b: { email: string; full_name: string; role: string }) =>
    request("/api/users", { method: "POST", body: JSON.stringify(b) }),
  // Sources
  listSources: (opts: { activeOnly?: boolean } = {}) =>
    request<Source[]>(`/api/sources?active_only=${opts.activeOnly === false ? "false" : "true"}`),
  createSource: (b: Partial<Source>) =>
    request<Source>("/api/sources", { method: "POST", body: JSON.stringify(b) }),
  updateSource: (id: string, b: Partial<Source>) =>
    request<Source>(`/api/sources/${id}`, { method: "PATCH", body: JSON.stringify(b) }),
  deleteSource: (id: string) =>
    request<void>(`/api/sources/${id}`, { method: "DELETE" }),
  // Runs
  listRuns: (status?: string) =>
    request<Run[]>(`/api/runs${status ? `?status_filter=${status}` : ""}`),
  runCounts: () =>
    request<{ total: number; by_status: Record<string, number> }>(`/api/runs/counts`),
  getRun: (id: string) => request<Run>(`/api/runs/${id}`),
  createRun: (b: { source_id: string; max_cost_cents: number }) =>
    request<Run>("/api/runs", { method: "POST", body: JSON.stringify(b) }),
  startRun: (id: string) =>
    request<RunStartResult>(`/api/runs/${id}/start`, { method: "POST" }),
  deleteRun: (id: string) =>
    request<void>(`/api/runs/${id}`, { method: "DELETE" }),
  listRunPages: (id: string, limit = 50) =>
    request<RunPage[]>(`/api/runs/${id}/pages?limit=${limit}`),
  exportRun: (id: string) =>
    request<ExportSummary>(`/api/runs/${id}/exports`, { method: "POST" }),
  listExports: (run_id: string) => request<ExportRow[]>(`/api/runs/${run_id}/exports`),
  // Contacts
  listContacts: (params: {
    run_id?: string;
    review_status?: string;
    dedupe_status?: string;
    has_email?: boolean;
    has_contact_info?: boolean;
    limit?: number;
  }) => {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
    }
    const qs = q.toString();
    return request<Contact[]>(`/api/contacts${qs ? `?${qs}` : ""}`);
  },
  reviewContact: (id: string, action: string, payload?: Record<string, unknown>) =>
    request<Contact>(`/api/contacts/${id}/review`, {
      method: "POST",
      body: JSON.stringify({ action, payload }),
    }),
  enrichContact: (id: string) =>
    request<EnrichmentRun>(`/api/contacts/${id}/enrich`, { method: "POST" }),
  getEnrichments: (id: string) =>
    request<EnrichmentSummary>(`/api/contacts/${id}/enrichments`),
  bulkEnrich: (contact_ids: string[]) =>
    request<BulkEnrichResult>(`/api/contacts/enrich/bulk`, {
      method: "POST",
      body: JSON.stringify({ contact_ids }),
    }),
  // Suppressions
  listSuppressions: () => request<Suppression[]>("/api/suppressions"),
  createSuppression: (b: { type: string; value: string; reason: string }) =>
    request<Suppression>("/api/suppressions", { method: "POST", body: JSON.stringify(b) }),
  deleteSuppression: (id: string) =>
    request<void>(`/api/suppressions/${id}`, { method: "DELETE" }),
  // Admin
  getMemory: (limit = 2016) => request<MemoryResponse>(`/api/admin/memory?limit=${limit}`),
};

export interface Me {
  id: string;
  email: string;
  full_name: string;
  role: string;
}

// ----- shared types (replace with codegen output of `pnpm run gen-api`) -----

export interface Source {
  id: string;
  name: string;
  url: string;
  segment_template: string | null;
  crawl_tier: number;
  follow_external_links: boolean;
  max_external_pages: number;
  active: boolean;
  created_by: string;
  created_at: string;
}

export interface Run {
  id: string;
  source_id: string;
  source_name: string | null;
  source_crawl_tier: number | null;
  status: string;
  max_cost_cents: number;
  estimated_cost_cents: number | null;
  cost_cents: number;
  tokens_used: number;
  budget_approved_by: string | null;
  budget_approved_at: string | null;
  pages_crawled: number;
  contacts_extracted: number;
  created_by: string;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
}

export interface EnrichmentSerpAttempt {
  query: string;
  provider: string;
  urls: string[];
  note: string;
}

export interface EnrichmentCrawledPage {
  url: string;
  status: string;
  http_status: number | null;
  error: string | null;
}

export interface EnrichmentRun {
  id: string;
  status: string;
  pages_crawled: number;
  contacts_extracted: number;
  cost_cents: number;
  max_cost_cents: number;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  seed_source: string | null;
  seed_url: string | null;
  serp_attempts: EnrichmentSerpAttempt[];
  crawled_pages: EnrichmentCrawledPage[];
}

export interface EnrichmentSummary {
  parent_contact_id: string;
  runs: EnrichmentRun[];
  suggested_contacts: Contact[];
}

export interface BulkEnrichResult {
  requested: number;
  started: number;
  skipped: { contact_id: string; reason: string }[];
}

export interface RunStartResult {
  // The /start endpoint is now fire-and-forget: it returns 202 Accepted
  // and the background thread drives the orchestrator. Progress lives on
  // the run row itself + the /pages endpoint.
  accepted: boolean;
  run_id: string;
  status: string;
}

export interface RunPage {
  id: string;
  url: string;
  canonical_url: string | null;
  http_status: number | null;
  status: string;
  fetched_at: string | null;
  created_at: string;
  contacts_extracted: number;
  organizations_extracted: number;
  error: string | null;
}

export interface Contact {
  id: string;
  run_id: string;
  organization_id: string | null;
  source_page_id: string | null;
  source_page_url: string | null;
  name: string;
  title: string | null;
  email: string | null;
  alt_email: string | null;
  email_confidence: string | null;
  phone: string | null;
  evidence_excerpt: string | null;
  evidence_verified: string;
  evidence_match_score: number | null;
  segment_tag: string | null;
  review_status: string;
  reviewed_by: string | null;
  reviewed_at: string | null;
  dedupe_status: string;
  dedupe_reason: string | null;
  crm_agent_user_id: number | null;
  crm_agency_id: string | null;
  score_role_match: boolean;
  score_state_match: boolean;
  score_has_direct_email: boolean;
  score_has_phone: boolean;
  score_has_travel_signal: boolean;
  previously_exported: boolean;
  created_at: string;
}

export interface MemorySnapshot {
  iso_ts: string;
  mem_total_kb: number;
  mem_available_kb: number;
  mem_free_kb: number;
  swap_total_kb: number;
  swap_free_kb: number;
  buffers_kb: number;
  cached_kb: number;
}

export interface MemorySummary {
  sample_count: number;
  window_start: string | null;
  window_end: string | null;
  peak_used_kb: number;
  avg_used_kb: number;
  current_used_kb: number;
  mem_total_kb: number;
  headroom_pct: number;
}

export interface MemoryResponse {
  current: MemorySnapshot | null;
  history: MemorySnapshot[];
  summary: MemorySummary | null;
}

export interface Suppression {
  id: string;
  type: string;
  value: string;
  reason: string;
  added_by: string;
  added_at: string;
}

export interface ExportRow {
  id: string;
  run_id: string;
  csv_format: string;
  file_path: string;
  row_count: number;
  created_by: string;
  created_at: string;
}

export interface ExportSummary {
  main: ExportRow;
  bdr_verify: ExportRow;
  skipped: Record<string, number>;
}
