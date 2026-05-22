"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import { api, type Contact, type ExportSummary, type Run, type RunPage } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/Badge";

// Mirrors backend/app/services/run_orchestrator.py:MAX_PAGES_PER_RUN.
// If the backend cap changes, update this too — it's only a UI hint.
const MAX_PAGES_PER_RUN = 1500;

export default function RunDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [run, setRun] = useState<Run | null>(null);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [pages, setPages] = useState<RunPage[]>([]);
  const [contactList, setContactList] = useState<Contact[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastExport, setLastExport] = useState<ExportSummary | null>(null);

  async function loadAll() {
    const r = await api.getRun(id);
    setRun(r);
    const [contacts, recentPages] = await Promise.all([
      api.listContacts({ run_id: id, limit: 500 }),
      api.listRunPages(id, 30),
    ]);
    const by: Record<string, number> = {};
    for (const c of contacts) {
      by[c.review_status] = (by[c.review_status] ?? 0) + 1;
    }
    setCounts(by);
    setContactList(contacts);
    setPages(recentPages);
  }

  useEffect(() => {
    loadAll();
    // Poll faster while a run is actively progressing so the live trail
    // feels live. Slow down once it's terminal to spare the backend.
    const intervalMs = run && ["pending", "running"].includes(run.status) ? 2000 : 8000;
    const t = setInterval(loadAll, intervalMs);
    return () => clearInterval(t);
  }, [id, run?.status]);

  async function start() {
    // The start endpoint returns 202 immediately and the orchestrator
    // runs in a background thread. We refresh once so the UI flips to
    // 'running' fast, then the existing polling effect takes over.
    setError(null);
    setBusy("start");
    try {
      await api.startRun(id);
      await loadAll();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function doExport() {
    setError(null);
    setBusy("export");
    try {
      setLastExport(await api.exportRun(id));
      await loadAll();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  if (!run) return <p>Loading…</p>;

  const isRunning = run.status === "running";
  const isTerminal = !["pending", "running"].includes(run.status);
  const budgetPct = Math.min(
    100,
    Math.round((run.cost_cents / Math.max(run.max_cost_cents, 1)) * 100),
  );
  const pagesPct = Math.min(
    100,
    Math.round((run.pages_crawled / MAX_PAGES_PER_RUN) * 100),
  );

  const totalReview = Object.values(counts).reduce((a, b) => a + b, 0);

  const tier = run.source_crawl_tier ?? 1;
  const tierName =
    tier === 2
      ? "Firecrawl (headless browser)"
      : tier === 3
      ? "Playwright fallback"
      : "httpx + trafilatura";

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-4">
        <div className="min-w-0">
          <h1 className="truncate">
            Run <code className="font-mono text-base">{run.id.slice(0, 8)}…</code>
          </h1>
          <p className="text-sm text-neutral-600 mt-1">
            Source: <strong>{run.source_name ?? "(unknown)"}</strong> · Crawling
            on <strong>Tier {tier}</strong> — {tierName}.
          </p>
        </div>
        <div className="flex gap-2 shrink-0">
          <Button
            onClick={start}
            disabled={busy !== null || run.status !== "pending"}
            variant={run.status === "pending" ? "default" : "secondary"}
          >
            {busy === "start" ? "Starting…" : "Start run"}
          </Button>
          <Button onClick={doExport} disabled={busy !== null} variant="secondary">
            {busy === "export" ? "Exporting…" : "Generate CSV"}
          </Button>
          <Link href={`/review?run_id=${run.id}`}>
            <Button variant="secondary">Review queue</Button>
          </Link>
        </div>
      </div>

      <StatusBanner run={run} />

      <Card>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
          <Stat label="Status" value={run.status} />
          <Stat label="Pages crawled" value={String(run.pages_crawled)} />
          {run.contacts_extracted > 0 ? (
            <Link href={`/review?run_id=${id}`} className="block group" title="Open review queue for this run">
              <Stat label="Contacts" value={String(run.contacts_extracted)} accent />
            </Link>
          ) : (
            <Stat label="Contacts" value={String(run.contacts_extracted)} />
          )}
          <Stat
            label="Cost / cap"
            value={`${run.cost_cents}¢ / ${run.max_cost_cents}¢`}
          />
        </div>

        <div className="mt-5 space-y-4">
          <ProgressBar
            label="Budget consumed"
            pct={budgetPct}
            right={`${run.cost_cents}¢ of ${run.max_cost_cents}¢ ($${(run.max_cost_cents / 100).toFixed(2)})`}
            tone={budgetPct >= 90 ? "warn" : "ok"}
          />
          <ProgressBar
            label="Pages crawled"
            pct={pagesPct}
            right={`${run.pages_crawled} of ${MAX_PAGES_PER_RUN}-page hard cap`}
            tone="ok"
          />
          {!isTerminal && (
            <p className="text-xs text-neutral-500">
              These bars show progress against hard caps — the crawler
              discovers links as it goes, so the actual finish point may be
              well before either bar hits 100%. Whichever cap is reached first
              ends the run.
            </p>
          )}
          <details className="text-xs text-neutral-600">
            <summary className="cursor-pointer font-medium text-neutral-800">
              How is this cost calculated?
            </summary>
            <div className="mt-2 space-y-2 max-w-prose">
              <p>
                Cost counts only the <strong>Sonnet 4.5 extraction calls</strong>{" "}
                the orchestrator makes after a page is fetched. Crawling itself
                is free regardless of tier — httpx (Tier 1) and the
                self-hosted Firecrawl (Tier 2) both run on this server with
                no per-request bill.
              </p>
              <p>
                After each Anthropic call, the response&apos;s{" "}
                <code>usage</code> object is multiplied by Sonnet 4.5&apos;s
                published per-token prices:
              </p>
              <ul className="list-disc pl-5">
                <li>
                  <strong>Input</strong> tokens — $3.00 / M (0.0003¢ each)
                </li>
                <li>
                  <strong>Cache write</strong> (5-min ephemeral) — $3.75 / M
                  (0.000375¢ each)
                </li>
                <li>
                  <strong>Cache read</strong> (warm hit on the system prompt) —{" "}
                  $0.30 / M (0.00003¢ each)
                </li>
                <li>
                  <strong>Output</strong> tokens — $15.00 / M (0.0015¢ each)
                </li>
              </ul>
              <p>
                The four are summed and{" "}
                <strong>rounded up to an integer cent</strong> (never
                undercharge against the cap), then added to{" "}
                <code>cost_cents</code>. Before every Anthropic call the
                worker checks{" "}
                <code>cost_cents + estimated_next_call &gt; max_cost_cents</code>{" "}
                — if true the run halts with{" "}
                <code>status=over_budget</code>; any pages not yet extracted
                just stay un-extracted, so you can rerun later with a higher
                cap and pick up where it stopped.
              </p>
              <p>
                The cache-read discount is the whole point of keeping a long
                stable system prompt — after the first call in a 5-minute
                window, the bulk of each subsequent page is billed at the
                0.1× rate, so typical pages land at a small fraction of a
                cent each.
              </p>
            </div>
          </details>
        </div>
      </Card>

      <Card>
        <div className="flex items-center justify-between">
          <h3>Crawl progress</h3>
          {isRunning && (
            <span className="text-xs text-neutral-500">
              live — polling every 2s
            </span>
          )}
        </div>
        {pages.length === 0 ? (
          <div className="mt-3 text-sm text-neutral-600 space-y-2 max-w-prose">
            {run.status === "pending" ? (
              <p>No pages yet. Click <strong>Start run</strong> to kick off the crawler.</p>
            ) : (
              <>
                <p>
                  <span className="font-medium text-neutral-800">
                    Waiting for the first page…
                  </span>{" "}
                  The orchestrator just started and is doing setup work
                  before any URLs show up here.
                </p>
                <ol className="list-decimal pl-5 space-y-1 text-xs">
                  <li>
                    Fetch the seed URL for{" "}
                    <strong>{run.source_name ?? "this source"}</strong> and
                    discover same-domain links from it (Tier{" "}
                    {run.source_crawl_tier ?? 1} renderer).
                  </li>
                  <li>
                    Check each link against <code>robots.txt</code> and the
                    SSRF guard (no private IPs, no non-http schemes).
                  </li>
                  <li>
                    Crawl up to {MAX_PAGES_PER_RUN} pages across ≤500 unique
                    domains, at 2 req/sec per domain, archiving raw HTML as
                    it goes.
                  </li>
                  <li>
                    Hand each fetched page to the extractor for contact
                    pulls, dedupe, and scoring — until the{" "}
                    {run.max_cost_cents}¢ budget cap is hit or the queue
                    drains.
                  </li>
                </ol>
                <p className="text-xs text-neutral-500">
                  First entry usually appears within ~5–15s of starting.
                </p>
              </>
            )}
          </div>
        ) : (
          <ul className="mt-3 divide-y text-sm">
            {pages.map((p) => (
              <li key={p.id} className="py-2 space-y-0.5">
                <div className="flex items-center gap-2">
                  <Badge intent={pageStatusIntent(p.status, p.http_status)}>
                    {p.status === "fetched" && p.http_status
                      ? p.http_status
                      : p.status}
                  </Badge>
                  <a
                    href={p.canonical_url ?? p.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="truncate flex-1 text-xs"
                    title={p.canonical_url ?? p.url}
                  >
                    {p.canonical_url ?? p.url}
                  </a>
                  <span className="text-xs text-neutral-500 shrink-0">
                    {relativeTime(p.created_at)}
                  </span>
                </div>
                <p className="text-xs text-neutral-500 pl-1">
                  {pageActionDescription(
                    p.status,
                    p.http_status,
                    p.contacts_extracted,
                    p.organizations_extracted,
                    p.error,
                  )}
                </p>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card>
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <h3>Leads found, awaiting review ({totalReview})</h3>
          {totalReview > 0 && (
            <Link href={`/review?run_id=${id}`}>
              <Button>Review</Button>
            </Link>
          )}
        </div>
        <p className="mt-1 text-sm text-neutral-600 max-w-prose">
          Every contact the extractor pulled from this run is shown below,
          grouped by review status. Click any badge to jump into the review
          queue filtered to just that bucket.
        </p>
        <dl className="mt-3 grid gap-x-6 gap-y-1 text-xs text-neutral-600 grid-cols-1 sm:grid-cols-2 max-w-prose">
          <div>
            <Badge intent="info">pending</Badge>{" "}
            <span>not yet looked at by a reviewer.</span>
          </div>
          <div>
            <Badge intent="warn">needs_review</Badge>{" "}
            <span>flagged by the scorer as borderline — eyeball before sending.</span>
          </div>
          <div>
            <Badge intent="ok">approved</Badge>{" "}
            <span>cleared for the outreach export.</span>
          </div>
          <div>
            <Badge intent="ok">edited</Badge>{" "}
            <span>approved after a reviewer tweaked the row.</span>
          </div>
          <div>
            <Badge intent="bad">rejected</Badge>{" "}
            <span>excluded from the outreach export.</span>
          </div>
        </dl>
        <div className="mt-3 flex gap-2 flex-wrap">
          {Object.entries(counts).map(([status, n]) => (
            <Link key={status} href={`/review?run_id=${id}&review_status=${status}`}>
              <Badge intent={badgeIntent(status)}>
                {status}: {n}
              </Badge>
            </Link>
          ))}
          {Object.keys(counts).length === 0 && (
            <p className="text-sm text-neutral-600">
              No leads extracted yet. Once the crawler finds contacts they&apos;ll
              show up here for review.
            </p>
          )}
        </div>
        {contactList.length > 0 && (
          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-neutral-50 text-xs uppercase text-neutral-500">
                <tr>
                  <th className="text-left px-3 py-2">Name</th>
                  <th className="text-left px-3 py-2">Title</th>
                  <th className="text-left px-3 py-2">Email</th>
                  <th className="text-left px-3 py-2">Status</th>
                  <th className="text-left px-3 py-2">Found on</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {contactList.slice(0, 50).map((c) => (
                  <tr key={c.id} className="hover:bg-neutral-50">
                    <td className="px-3 py-2 font-medium">
                      <Link
                        href={`/review?run_id=${id}&review_status=${c.review_status}`}
                        className="underline underline-offset-2"
                        title="Open this contact in the review queue"
                      >
                        {c.name}
                      </Link>
                    </td>
                    <td className="px-3 py-2 text-neutral-700">{c.title ?? "—"}</td>
                    <td className="px-3 py-2 text-neutral-700 text-xs">
                      {c.email ?? <span className="text-neutral-400">none</span>}
                    </td>
                    <td className="px-3 py-2">
                      <Badge intent={badgeIntent(c.review_status)}>
                        {c.review_status}
                      </Badge>
                    </td>
                    <td className="px-3 py-2 text-xs">
                      {c.source_page_url ? (
                        <a
                          href={c.source_page_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-neutral-600 underline underline-offset-2 truncate inline-block max-w-[20rem]"
                          title={c.source_page_url}
                        >
                          {c.source_page_url}
                        </a>
                      ) : (
                        <span className="text-neutral-400">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {contactList.length > 50 && (
              <p className="mt-2 text-xs text-neutral-500">
                Showing 50 of {contactList.length}.{" "}
                <Link href={`/review?run_id=${id}`} className="underline">
                  Open the full review queue →
                </Link>
              </p>
            )}
          </div>
        )}
      </Card>

      {lastExport && (
        <Card>
          <h3>Last export</h3>
          <div className="mt-3 space-y-2 text-sm">
            <div>
              Main: <code>{lastExport.main.row_count} rows</code> →{" "}
              <a href={`/api/exports/${lastExport.main.id}/download`}>download</a>
            </div>
            <div>
              BDR-verify: <code>{lastExport.bdr_verify.row_count} rows</code> →{" "}
              <a href={`/api/exports/${lastExport.bdr_verify.id}/download`}>download</a>
            </div>
            {Object.keys(lastExport.skipped).length > 0 && (
              <details>
                <summary className="cursor-pointer text-neutral-600">
                  Skipped ({Object.values(lastExport.skipped).reduce((a, b) => a + b, 0)})
                </summary>
                <ul className="mt-2 text-xs text-neutral-600 pl-4 list-disc">
                  {Object.entries(lastExport.skipped).map(([k, v]) => (
                    <li key={k}>
                      <code>{k}</code>: {v}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        </Card>
      )}

      {error && (
        <Card>
          <p className="text-red-700">{error}</p>
        </Card>
      )}
    </div>
  );
}

function Stat({ label, value, accent = false }: { label: string; value: string; accent?: boolean }) {
  return (
    <div>
      <div className="text-xs uppercase text-neutral-500">{label}</div>
      <div
        className={
          accent
            ? "text-base font-medium underline underline-offset-2 group-hover:text-blue-700"
            : "text-base font-medium"
        }
      >
        {value}
      </div>
    </div>
  );
}

function ProgressBar({
  label,
  pct,
  right,
  tone,
}: {
  label: string;
  pct: number;
  right: string;
  tone: "ok" | "warn";
}) {
  const fill =
    tone === "warn" ? "bg-amber-500" : "bg-emerald-500";
  return (
    <div>
      <div className="flex items-baseline justify-between text-xs">
        <span className="font-medium text-neutral-700">
          {label} <span className="text-neutral-500">— {pct}%</span>
        </span>
        <span className="text-neutral-500">{right}</span>
      </div>
      <div className="mt-1 h-2 w-full overflow-hidden rounded-full bg-neutral-100">
        <div
          className={`h-full ${fill} transition-[width] duration-500`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function badgeIntent(s: string): "ok" | "warn" | "bad" | "info" | "neutral" {
  if (s === "approved" || s === "edited") return "ok";
  if (s === "needs_review") return "warn";
  if (s === "rejected") return "bad";
  if (s === "pending") return "info";
  return "neutral";
}

function pageStatusIntent(
  status: string,
  http: number | null,
): "ok" | "warn" | "bad" | "info" | "neutral" {
  if (status === "fetched") {
    if (http && http >= 400) return "bad";
    if (http && http >= 300) return "warn";
    return "ok";
  }
  if (status.startsWith("blocked_")) return "warn";
  if (status === "error") return "bad";
  return "neutral";
}

function pageActionDescription(
  status: string,
  http: number | null,
  contacts: number,
  orgs: number,
  error: string | null = null,
): string {
  if (status === "fetched") {
    if (http && http >= 500) return `Fetched, but the server returned ${http} — page body discarded.`;
    if (http && http >= 400) return `Fetched, but the server returned ${http} — page body discarded.`;
    if (http && http >= 300) return `Server redirected (${http}); followed and recorded the final URL.`;
    const yieldParts: string[] = [];
    if (contacts > 0) yieldParts.push(`${contacts} contact${contacts === 1 ? "" : "s"}`);
    if (orgs > 0) yieldParts.push(`${orgs} organization${orgs === 1 ? "" : "s"}`);
    if (yieldParts.length > 0) {
      return `Downloaded and extracted — found ${yieldParts.join(" and ")}.`;
    }
    return "Downloaded the page; the extractor found no contacts on it (or it's still being processed).";
  }
  if (status === "blocked_robots") {
    return "Skipped — the site's robots.txt disallows this path for our crawler.";
  }
  if (status === "blocked_ssrf") {
    return "Skipped — the URL resolved to a private/forbidden target (SSRF guard).";
  }
  if (status === "blocked_redirect_limit") {
    return "Skipped — too many redirects in a row; gave up to avoid a loop.";
  }
  if (status === "error") {
    const base = "Fetch failed (network error, timeout, or invalid response); nothing extracted from this URL.";
    return error ? `${base} (${error})` : base;
  }
  return "";
}

function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  const now = Date.now();
  const secs = Math.max(0, Math.round((now - then) / 1000));
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  return `${hours}h ago`;
}


function StatusBanner({ run }: { run: Run }) {
  // The single most-asked question on this page is "is it still working or
  // is it done?" — make that answerable from across the room. Color +
  // icon + headline + a one-line "what now" sentence per status.
  const s = run.status;

  const styles: Record<string, { wrap: string; dot: string; icon: string; title: string }> = {
    pending: {
      wrap: "border-amber-300 bg-amber-50 text-amber-900",
      dot: "bg-amber-500",
      icon: "\u23F8",
      title: "Not started yet",
    },
    running: {
      wrap: "border-blue-300 bg-blue-50 text-blue-900",
      dot: "bg-blue-500 animate-pulse",
      icon: "\u25B6",
      title: "Running now",
    },
    completed: {
      wrap: "border-emerald-300 bg-emerald-50 text-emerald-900",
      dot: "bg-emerald-500",
      icon: "\u2713",
      title: "Finished",
    },
    failed: {
      wrap: "border-red-300 bg-red-50 text-red-900",
      dot: "bg-red-500",
      icon: "\u2717",
      title: "Failed",
    },
    over_budget: {
      wrap: "border-amber-300 bg-amber-50 text-amber-900",
      dot: "bg-amber-500",
      icon: "\u26A0",
      title: "Stopped \u2014 budget cap reached",
    },
  };
  const style = styles[s] ?? {
    wrap: "border-neutral-300 bg-neutral-50 text-neutral-900",
    dot: "bg-neutral-400",
    icon: "\u25CB",
    title: s,
  };

  let detail: React.ReactNode = null;
  if (s === "pending") {
    detail = (
      <>
        Click <strong>Start run</strong> above to kick off the crawler.
      </>
    );
  } else if (s === "running" && run.started_at) {
    detail = (
      <>
        Started <strong>{runDuration(run.started_at, new Date().toISOString())} ago</strong>{" "}
        \u00B7 <strong>{run.pages_crawled}</strong> page{run.pages_crawled === 1 ? "" : "s"}{" "}
        fetched, <strong>{run.contacts_extracted}</strong> lead{run.contacts_extracted === 1 ? "" : "s"}{" "}
        so far. This page polls every 2s.
      </>
    );
  } else if (s === "completed" && run.completed_at) {
    const dur = run.started_at ? runDuration(run.started_at, run.completed_at) : "?";
    const finishedAgo = runDuration(run.completed_at, new Date().toISOString());
    detail = (
      <>
        Finished <strong>{finishedAgo} ago</strong> \u00B7 ran for{" "}
        <strong>{dur}</strong> \u00B7 pulled <strong>{run.contacts_extracted}</strong>{" "}
        lead{run.contacts_extracted === 1 ? "" : "s"} from{" "}
        <strong>{run.pages_crawled}</strong> page{run.pages_crawled === 1 ? "" : "s"}.
      </>
    );
  } else if (s === "failed" && run.completed_at) {
    const finishedAgo = runDuration(run.completed_at, new Date().toISOString());
    detail = (
      <>
        Stopped <strong>{finishedAgo} ago</strong> after{" "}
        <strong>{run.pages_crawled}</strong> page{run.pages_crawled === 1 ? "" : "s"}.{" "}
        Check backend logs for the orchestrator traceback.
      </>
    );
  } else if (s === "over_budget") {
    detail = (
      <>
        Spent <strong>{run.cost_cents}\u00A2 of {run.max_cost_cents}\u00A2</strong>{" "}
        before the cap halted extraction. Unprocessed pages stayed un-extracted \u2014
        rerun with a higher cap to pick up where it stopped.
      </>
    );
  }

  return (
    <div
      role="status"
      aria-live={s === "running" ? "polite" : "off"}
      className={`flex items-start gap-3 rounded-lg border px-4 py-3 ${style.wrap}`}
    >
      <span className={`mt-1 inline-block h-2.5 w-2.5 shrink-0 rounded-full ${style.dot}`} aria-hidden />
      <div className="min-w-0">
        <div className="flex items-baseline gap-2">
          <span className="text-lg" aria-hidden>{style.icon}</span>
          <span className="font-semibold text-base">{style.title}</span>
          <code className="font-mono text-xs opacity-70">({s})</code>
        </div>
        {detail && <div className="text-sm mt-0.5">{detail}</div>}
      </div>
    </div>
  );
}

function runDuration(start: string, end: string): string {
  const ms = Math.max(0, new Date(end).getTime() - new Date(start).getTime());
  const secs = Math.round(ms / 1000);
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  const remSecs = secs % 60;
  if (mins < 60) return remSecs ? `${mins}m ${remSecs}s` : `${mins}m`;
  const hours = Math.floor(mins / 60);
  const remMins = mins % 60;
  return remMins ? `${hours}h ${remMins}m` : `${hours}h`;
}
