"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";
import { api, type Contact, type EnrichmentSummary } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Select } from "@/components/NativeSelect";
import { Badge } from "@/components/Badge";

function badge(s: string) {
  if (s === "approved" || s === "edited") return <Badge intent="ok">{s}</Badge>;
  if (s === "needs_review") return <Badge intent="warn">{s}</Badge>;
  if (s === "rejected") return <Badge intent="bad">{s}</Badge>;
  if (s === "verified") return <Badge intent="ok">{s}</Badge>;
  if (s === "fuzzy") return <Badge intent="info">{s}</Badge>;
  if (s === "failed") return <Badge intent="bad">{s}</Badge>;
  return <Badge>{s}</Badge>;
}

function emailConfidenceIntent(s: string): "ok" | "warn" | "bad" | "info" | "neutral" {
  if (s === "verified") return "ok";
  if (s === "pattern_guess") return "warn";
  // Catch-all: domain accepts any local-part at SMTP, validator can't
  // distinguish real mailbox from anything-goes. Color it "bad" so it
  // visually separates from "verified" — sending here risks deliverability.
  if (s === "unverifiable_catchall") return "bad";
  return "neutral";
}

function emailConfidenceTitle(s: string): string {
  if (s === "verified") return "Confirmed via validator — safe to send";
  if (s === "pattern_guess")
    return "Inferred from a domain email pattern — verify before sending";
  if (s === "unverifiable_catchall")
    return (
      "Domain is a catch-all (SMTP accepts any local-part) — validator can't " +
      "confirm this mailbox exists. Sending risks soft-bounce or spam complaint."
    );
  return s;
}

/**
 * Build a plain-text snapshot of a contact for pasting into an AI agent.
 * Markdown-ish key:value format works well for both human reading and LLM
 * parsing; no fenced code blocks because the user is likely already pasting
 * INTO an LLM chat where the message itself becomes the prompt.
 *
 * Fields are ordered "identity first, then context, then provenance" so an
 * agent can stop reading once it has what it needs.
 */
function formatContactForAI(c: Contact): string {
  const lines: string[] = [];
  lines.push("ProspectForge contact record (Acme Acme Travel)");
  lines.push("");
  lines.push(`Name: ${c.name}`);
  lines.push(`Title: ${c.title ?? "(unknown)"}`);
  lines.push(`Email: ${c.email ?? "(none on file)"}`);
  if (c.email && c.email_confidence) {
    lines.push(`  Email confidence: ${c.email_confidence}`);
  }
  if (c.alt_email) lines.push(`Alt email: ${c.alt_email}`);
  lines.push(`Phone: ${c.phone ?? "(none on file)"}`);
  if (c.segment_tag) lines.push(`Segment: ${c.segment_tag}`);
  lines.push("");
  lines.push(`Dedupe status: ${c.dedupe_status}`);
  if (c.dedupe_reason) lines.push(`  Reason: ${c.dedupe_reason}`);
  lines.push(`Review status: ${c.review_status}`);
  lines.push(
    `Previously exported: ${c.previously_exported ? "yes" : "no"}`,
  );
  lines.push("");
  lines.push("Source provenance:");
  lines.push(
    `  Page URL: ${c.source_page_url ?? "(unknown)"}`,
  );
  if (c.evidence_excerpt) {
    lines.push(
      `  Evidence (${c.evidence_verified}${c.evidence_match_score !== null ? `, score ${c.evidence_match_score.toFixed(2)}` : ""}):`,
    );
    // Indent each line of the excerpt so paragraph breaks don't merge
    // visually with the rest of the format.
    const indented = c.evidence_excerpt
      .split("\n")
      .map((l) => `  > ${l}`)
      .join("\n");
    lines.push(indented);
  }
  return lines.join("\n");
}

/**
 * Compact status pill that sits next to the panel title so the operator
 * sees at-a-glance whether anything is still running. Three states:
 *   - "running" (blue, animated dot) — at least one run is pending/running
 *   - "complete" (green) — all runs concluded, ≥1 succeeded
 *   - "failed" (red) — all runs failed (no successes, none in flight)
 */
function EnrichmentStatusPill({
  activeRun,
  completedRuns,
  failedRuns,
  totalRuns,
}: {
  activeRun: boolean;
  completedRuns: number;
  failedRuns: number;
  totalRuns: number;
}) {
  if (totalRuns === 0) return null;
  if (activeRun) {
    return (
      <span
        className="inline-flex items-center gap-1.5 rounded-full bg-blue-100 px-2 py-0.5 text-xs font-bold text-blue-900"
        title={`At least one enrichment run is still in progress (${completedRuns}/${totalRuns} done)`}
      >
        <span className="inline-block h-1.5 w-1.5 rounded-full bg-blue-500 animate-pulse" />
        Running
      </span>
    );
  }
  if (completedRuns > 0) {
    return (
      <span
        className="inline-flex items-center gap-1 rounded-full bg-brand-green-light/20 px-2 py-0.5 text-xs font-bold text-brand-green"
        title={`All enrichment runs concluded (${completedRuns}/${totalRuns} succeeded)`}
      >
        Complete
        {failedRuns > 0 && (
          <span className="font-normal text-amber-700">
            {" "}({failedRuns} failed)
          </span>
        )}
      </span>
    );
  }
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full bg-red-100 px-2 py-0.5 text-xs font-bold text-red-900"
      title={`All enrichment runs failed (${failedRuns}/${totalRuns})`}
    >
      Failed
    </span>
  );
}

/**
 * Loose name match for "is this suggestion about the same person as the
 * parent contact?" — case-insensitive, whitespace-collapsed, strips
 * common honorifics. We don't try harder than that; false positives
 * (a same-name different person) just show "Found email" which Morgan
 * verifies before applying.
 */
function normalizeName(name: string | null | undefined): string {
  if (!name) return "";
  return name
    .toLowerCase()
    .replace(/\b(dr|mr|mrs|ms|mx|prof|professor|rev|sir|dame)\.?\b/g, "")
    .replace(/[^a-z0-9 ]/g, " ")
    .split(/\s+/)
    .filter(Boolean)
    .join(" ");
}

function seedSourceLabel(s: string): string {
  if (s === "org_website") return "org website";
  if (s === "serp_site") return "SERP on org domain";
  if (s === "serp_freesearch") return "web search";
  return s;
}

function formatBulkCost(n: number): string {
  // Per-run budget cap is $0.50 (DEFAULT_ENRICHMENT_BUDGET_CENTS=50 in
  // services/enrichment_run.py). Actual cost is usually much less because
  // small targeted crawls don't exhaust the cap.
  const max = (n * 0.5).toFixed(2);
  return `up to $${max}`;
}

function dedupeBadge(s: string) {
  if (s === "exact" || s === "existing_customer" || s === "suppressed")
    return <Badge intent="bad">{s}</Badge>;
  if (s === "possible_match" || s === "domain_match" || s === "weak_match")
    return <Badge intent="warn">{s}</Badge>;
  if (s === "new") return <Badge intent="ok">{s}</Badge>;
  return <Badge>{s}</Badge>;
}

export default function ReviewPage() {
  return (
    <Suspense fallback={<p>Loading…</p>}>
      <ReviewInner />
    </Suspense>
  );
}

function ReviewInner() {
  const params = useSearchParams();
  const urlRunId = params.get("run_id") ?? "";
  const initialStatus = params.get("review_status") ?? "pending";

  const [runId, setRunId] = useState(urlRunId);
  const [reviewStatus, setReviewStatus] = useState(initialStatus);
  // Keep the run scope locked to the URL's run_id. Navigating in from a
  // run's "Review queue" link (/review?run_id=…) must show ONLY that run's
  // contacts — and stay correct even on a client-side nav that re-renders
  // this page without remounting it (where a mount-only initializer would
  // otherwise go stale and leak in other runs' rows).
  useEffect(() => {
    setRunId(urlRunId);
  }, [urlRunId]);
  const [dedupeStatus, setDedupeStatus] = useState("");
  const [enrichmentOutcome, setEnrichmentOutcome] = useState<
    "" | "succeeded" | "attempted_no_result"
  >("");
  // Default is "any" (All); the user's last choice is persisted to
  // localStorage and rehydrated on mount.
  const [contactInfo, setContactInfo] = useState<"any" | "reachable" | "none">("any");
  const [contactInfoHydrated, setContactInfoHydrated] = useState(false);
  // Hydrate the contactInfo filter from localStorage on first client
  // render. We intentionally skip the first auto-load until hydration
  // completes so we don't fire an extra API call with the wrong default.
  useEffect(() => {
    const saved = window.localStorage.getItem("pf.review.contactInfo");
    if (saved === "any" || saved === "reachable" || saved === "none") {
      setContactInfo(saved);
    }
    setContactInfoHydrated(true);
  }, []);
  useEffect(() => {
    if (!contactInfoHydrated) return;
    window.localStorage.setItem("pf.review.contactInfo", contactInfo);
  }, [contactInfo, contactInfoHydrated]);
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [sorting, setSorting] = useState<SortingState>([]);
  const [selected, setSelected] = useState<Contact | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const [bulkEnriching, setBulkEnriching] = useState(false);
  const [bulkEnrichResult, setBulkEnrichResult] = useState<string | null>(null);
  const [copiedForAI, setCopiedForAI] = useState(false);

  // ESC closes the modal + lock body scroll while it's open so the page
  // underneath doesn't drift. Restoring on close keeps the row position.
  useEffect(() => {
    if (!selected) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSelected(null);
    };
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [selected]);

  // Reset the copied-flag any time the user switches contact in the modal,
  // so reopening on a different row shows "Copy for AI" not "Copied!".
  useEffect(() => {
    setCopiedForAI(false);
  }, [selected?.id]);

  async function copyForAI() {
    if (!selected) return;
    try {
      await navigator.clipboard.writeText(formatContactForAI(selected));
      setCopiedForAI(true);
      setTimeout(() => setCopiedForAI(false), 2000);
    } catch (e) {
      alert(`Couldn't copy to clipboard: ${(e as Error).message}`);
    }
  }

  async function load() {
    const rows = await api.listContacts({
      run_id: runId || undefined,
      review_status: reviewStatus || undefined,
      dedupe_status: dedupeStatus || undefined,
      has_contact_info:
        contactInfo === "reachable" ? true : contactInfo === "none" ? false : undefined,
      enrichment: enrichmentOutcome || undefined,
      limit: 200,
    });
    setContacts(rows);
  }
  useEffect(() => {
    // Wait for localStorage rehydration before the first auto-load so we
    // don't fire an extra request with the default filter then re-fire
    // with the saved one immediately after.
    if (!contactInfoHydrated) return;
    load();
  }, [runId, reviewStatus, dedupeStatus, contactInfo, enrichmentOutcome, contactInfoHydrated]);

  async function bulkEnrich() {
    if (contacts.length === 0) return;
    const ids = contacts.map((c) => c.id);
    const cost = formatBulkCost(ids.length);
    if (
      !confirm(
        `Start ${ids.length} enrichment runs on the contacts currently visible?\n\n` +
          `Approximate cost: ${cost} (per-run budget cap is $0.50; actual ` +
          `cost is typically lower).\n\n` +
          `Each runs in the background. Suggestions appear under each contact ` +
          `in the detail modal when ready.\n\n` +
          `Contacts whose organization has no website, no domain, and no ` +
          `findable presence via web search will be skipped (you'll see a count).`,
      )
    ) {
      return;
    }
    setBulkEnriching(true);
    setBulkEnrichResult(null);
    try {
      // The backend caps each request at 500 ids; if a future view ever
      // exceeds that we'd need to batch, but the listContacts limit is
      // 200 today so we're well inside.
      const result = await api.bulkEnrich(ids);
      const parts = [`Started ${result.started}`];
      if (result.skipped.length > 0) {
        parts.push(`skipped ${result.skipped.length} (no seedable org)`);
      }
      setBulkEnrichResult(parts.join(" · "));
    } catch (e) {
      setBulkEnrichResult(`Error: ${(e as Error).message}`);
    } finally {
      setBulkEnriching(false);
    }
  }

  async function act(c: Contact, action: string) {
    setBusy(c.id);
    try {
      await api.reviewContact(c.id, action);
      await load();
      if (selected?.id === c.id) setSelected(null);
    } catch (e) {
      alert((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function exportApprovedCsv() {
    setExportError(null);
    setExporting(true);
    try {
      const rows = await api.listContacts({
        run_id: runId || undefined,
        review_status: "approved",
        dedupe_status: dedupeStatus || undefined,
        has_contact_info:
          contactInfo === "reachable" ? true : contactInfo === "none" ? false : undefined,
        limit: 500,
      });
      if (rows.length === 0) {
        setExportError(
          "No approved contacts match the current run/dedupe filter — nothing to export.",
        );
        return;
      }
      const csv = buildCsv(rows);
      const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
      const suffix = runId ? `run-${runId.slice(0, 8)}-` : "";
      downloadCsv(csv, `approved-contacts-${suffix}${stamp}.csv`);
    } catch (e) {
      setExportError((e as Error).message);
    } finally {
      setExporting(false);
    }
  }

  const cols = useMemo<ColumnDef<Contact>[]>(
    () => [
      {
        header: "Name",
        accessorKey: "name",
        cell: (c) => (
          <div className="flex flex-col gap-0.5">
            <button
              className="text-left underline-offset-2 hover:underline"
              onClick={() => setSelected(c.row.original)}
            >
              {c.row.original.name}
            </button>
            {c.row.original.previously_exported && (
              <Badge intent="info">previously exported</Badge>
            )}
          </div>
        ),
      },
      { header: "Title", accessorKey: "title" },
      { header: "Email", accessorKey: "email" },
      {
        header: "Phone",
        accessorKey: "phone",
        cell: (c) => (
          <span className="font-mono text-xs">
            {c.row.original.phone ?? <span className="text-neutral-400">—</span>}
          </span>
        ),
      },
      { header: "Segment", accessorKey: "segment_tag" },
      {
        header: "Review",
        accessorKey: "review_status",
        cell: (c) => badge(c.row.original.review_status),
      },
      {
        header: "Dedupe",
        accessorKey: "dedupe_status",
        cell: (c) => dedupeBadge(c.row.original.dedupe_status),
      },
      {
        header: "Evidence",
        accessorKey: "evidence_verified",
        cell: (c) => {
          const row = c.row.original;
          const ex = row.evidence_excerpt || "";
          const truncated = ex.length > 120 ? ex.slice(0, 117) + "…" : ex;
          return (
            <div
              className="cursor-pointer max-w-md"
              onClick={() => setSelected(row)}
              title="Click to see full evidence + source URL"
            >
              {badge(row.evidence_verified)}
              {ex && (
                <p className="mt-1 text-xs italic text-muted-foreground line-clamp-2">
                  &ldquo;{truncated}&rdquo;
                </p>
              )}
            </div>
          );
        },
      },
      {
        header: () => (
          <span className="group relative inline-flex items-center gap-1 cursor-help">
            Score <span className="text-neutral-400">?</span>
            <div
              role="tooltip"
              className="pointer-events-none absolute right-0 top-full z-50 mt-1 hidden w-80 rounded-md border border-neutral-200 bg-white p-3 text-left text-xs font-normal text-neutral-700 shadow-lg group-hover:block"
            >
              <p className="mb-2 font-semibold text-neutral-900">
                Sum of 5 signals the scorer checks for each contact
              </p>
              <ul className="space-y-1.5">
                <li>
                  <span className="font-medium text-neutral-900">Role match</span>{" "}
                  — title looks like an educator/decision-maker we sell to.
                </li>
                <li>
                  <span className="font-medium text-neutral-900">State match</span>{" "}
                  — organization state matches a target state.
                </li>
                <li>
                  <span className="font-medium text-neutral-900">Direct email</span>{" "}
                  — a real address (not a pattern guess).
                </li>
                <li>
                  <span className="font-medium text-neutral-900">Phone</span>{" "}
                  — a phone number was extracted.
                </li>
                <li>
                  <span className="font-medium text-neutral-900">Travel signal</span>{" "}
                  — the source mentions trips, tours, performance travel, etc.
                </li>
              </ul>
            </div>
          </span>
        ),
        id: "score",
        accessorFn: (c) =>
          [
            c.score_role_match,
            c.score_state_match,
            c.score_has_direct_email,
            c.score_has_phone,
            c.score_has_travel_signal,
          ].filter(Boolean).length,
        cell: (c) => <ScoreCell contact={c.row.original} />,
      },
      {
        header: "Actions",
        id: "actions",
        cell: (c) => (
          <div className="flex gap-1">
            <Button
              variant="default"
              disabled={busy !== null}
              onClick={() => act(c.row.original, "approve")}
            >
              ✓
            </Button>
            <Button
              variant="destructive"
              disabled={busy !== null}
              onClick={() => act(c.row.original, "reject")}
            >
              ✗
            </Button>
          </div>
        ),
      },
    ],
    [busy],
  );

  const table = useReactTable({
    data: contacts,
    columns: cols,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-baseline gap-3 flex-wrap">
          <h1>Review queue</h1>
          <span className="text-sm text-neutral-600">
            <strong>{contacts.length}</strong> matching{" "}
            {contacts.length === 1 ? "contact" : "contacts"}
            {contacts.length === 200 && (
              <span
                className="text-neutral-400"
                title="The backend caps each request at 200 rows. Narrow the filters to see beyond this slice."
              >
                {" "}(capped)
              </span>
            )}
          </span>
          {bulkEnrichResult && (
            <span className="text-sm text-brand-green">{bulkEnrichResult}</span>
          )}
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <Button
            variant="secondary"
            onClick={bulkEnrich}
            disabled={bulkEnriching || contacts.length === 0}
            title={
              contacts.length === 0
                ? "No contacts in the current filter view"
                : `Run enrichment on the ${contacts.length} contacts currently visible. Approximate cost: ${formatBulkCost(contacts.length)}. Each enrichment runs in the background; suggestions appear under each contact in the detail modal when ready.`
            }
          >
            {bulkEnriching
              ? "Starting…"
              : `Enrich ${contacts.length} visible`}
          </Button>
          <Button
            variant="secondary"
            onClick={exportApprovedCsv}
            disabled={exporting}
            title="Download all approved contacts that match the current run and dedupe filters as a CSV"
          >
            {exporting ? "Exporting…" : "Export approved as CSV"}
          </Button>
          <Select value={reviewStatus} onChange={(e) => setReviewStatus(e.target.value)}>
            <option value="">(any review)</option>
            <option value="pending">pending</option>
            <option value="approved">approved</option>
            <option value="rejected">rejected</option>
            <option value="edited">edited</option>
            <option value="needs_review">needs_review</option>
          </Select>
          <Select
            value={contactInfo}
            onChange={(e) => setContactInfo(e.target.value as "any" | "reachable" | "none")}
            title="Filter rows by whether the contact has an email or a phone number"
          >
            <option value="reachable">Has email or phone</option>
            <option value="any">All</option>
            <option value="none">No contact info</option>
          </Select>
          <Select value={dedupeStatus} onChange={(e) => setDedupeStatus(e.target.value)}>
            <option value="">(any dedupe)</option>
            <option value="new">new</option>
            <option value="possible_match">possible_match</option>
            <option value="weak_match">weak_match</option>
            <option value="domain_match">domain_match</option>
            <option value="exact">exact</option>
            <option value="existing_customer">existing_customer</option>
            <option value="suppressed">suppressed</option>
          </Select>
          <Select
            value={enrichmentOutcome}
            onChange={(e) =>
              setEnrichmentOutcome(
                e.target.value as "" | "succeeded" | "attempted_no_result",
              )
            }
            title="Filter by enrichment outcome. 'Info found' means an enrichment produced an email suggestion for the contact (even if the crawl hit its budget cap); 'nothing found' means enrichment was attempted but produced no email."
          >
            <option value="">(any enrichment)</option>
            <option value="succeeded">Enriched: info found</option>
            <option value="attempted_no_result">Enriched: nothing found</option>
          </Select>
        </div>
      </div>
      {runId && (
        <div className="flex items-center justify-between gap-3 flex-wrap rounded border border-brand-blue/30 bg-brand-blue-light/10 px-3 py-2 text-sm">
          <span className="text-neutral-700">
            Scoped to a single run —{" "}
            <code className="font-mono text-xs bg-white px-1 py-0.5 rounded border">
              {runId.slice(0, 8)}…
            </code>
            . Only contacts from this run are shown.
          </span>
          <Link
            href="/review"
            className="text-brand-blue hover:underline shrink-0"
            title="Clear the run filter and show contacts across all runs"
          >
            Show all runs
          </Link>
        </div>
      )}
      {exportError && (
        <p className="text-sm text-red-700">{exportError}</p>
      )}

      <Card className="overflow-x-auto p-0">
        <table className="w-full text-sm">
          <thead className="bg-neutral-50 text-xs uppercase text-neutral-500">
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id}>
                {hg.headers.map((h) => (
                  <th
                    key={h.id}
                    onClick={h.column.getToggleSortingHandler()}
                    className="text-left px-3 py-2 cursor-pointer select-none"
                  >
                    {flexRender(h.column.columnDef.header, h.getContext())}
                    {{ asc: " ↑", desc: " ↓" }[h.column.getIsSorted() as string] ?? ""}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody className="divide-y">
            {table.getRowModel().rows.map((r) => (
              <tr key={r.id} className="hover:bg-neutral-50">
                {r.getVisibleCells().map((cell) => (
                  <td key={cell.id} className="px-3 py-2">
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))}
            {contacts.length === 0 && (
              <tr>
                <td colSpan={cols.length} className="text-center py-4 text-neutral-600">
                  No contacts match.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </Card>

      {selected && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="contact-detail-title"
          className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 p-4 overflow-y-auto"
          onClick={(e) => {
            // Click the backdrop (not the modal body) to close.
            if (e.target === e.currentTarget) setSelected(null);
          }}
        >
        <Card className="mt-12 w-full max-w-2xl shadow-xl">
          <div className="flex items-center justify-between gap-3">
            <h3 id="contact-detail-title">{selected.name}</h3>
            <div className="flex items-center gap-2">
              <Button
                variant="secondary"
                onClick={copyForAI}
                title="Copy this contact's details to the clipboard as plain-text key:value pairs, ready to paste into Claude / ChatGPT / any AI agent"
              >
                {copiedForAI ? "Copied!" : "Copy for AI"}
              </Button>
              <Button variant="ghost" onClick={() => setSelected(null)}>
                Close
              </Button>
            </div>
          </div>
          <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
            <dt className="text-neutral-500">Title</dt>
            <dd>{selected.title ?? "—"}</dd>
            <dt className="text-neutral-500">Email</dt>
            <dd>
              {selected.email ?? "—"}
              {selected.email_confidence && (
                <span title={emailConfidenceTitle(selected.email_confidence)}>
                  <Badge intent={emailConfidenceIntent(selected.email_confidence)}>
                    {selected.email_confidence}
                  </Badge>
                </span>
              )}
            </dd>
            <dt className="text-neutral-500">Phone</dt>
            <dd>{selected.phone ?? "—"}</dd>
            <dt className="text-neutral-500">Source</dt>
            <dd>
              {selected.source_page_url ? (
                <a
                  href={selected.source_page_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-xs break-all"
                >
                  {selected.source_page_url}
                </a>
              ) : (
                "—"
              )}
            </dd>
            <dt className="text-neutral-500">Evidence</dt>
            <dd>
              <div className="flex items-center gap-2">
                {badge(selected.evidence_verified)}
                {selected.evidence_match_score !== null && (
                  <span className="text-xs text-neutral-500">
                    score {selected.evidence_match_score.toFixed(2)}
                  </span>
                )}
              </div>
              <blockquote className="mt-1 italic border-l-2 border-neutral-300 pl-3 text-neutral-700">
                {selected.evidence_excerpt ?? "—"}
              </blockquote>
            </dd>
            <dt className="text-neutral-500">Dedupe</dt>
            <dd>
              {dedupeBadge(selected.dedupe_status)}
              {selected.dedupe_reason && (
                <p className="mt-1 text-xs text-neutral-600">{selected.dedupe_reason}</p>
              )}
            </dd>
            <dt className="text-neutral-500">Export history</dt>
            <dd>
              {selected.previously_exported ? (
                <div className="flex items-center gap-2">
                  <Badge intent="info">previously exported</Badge>
                  <span className="text-xs text-neutral-600">
                    A prior export already emitted this contact (matched by
                    contact_id or normalized email). Re-export will be
                    skipped unless a reviewer explicitly overrides.
                  </span>
                </div>
              ) : (
                <span className="text-xs text-neutral-500">
                  Not previously exported.
                </span>
              )}
            </dd>
          </dl>
          <EnrichmentPanel contact={selected} />
        </Card>
        </div>
      )}
    </div>
  );
}

function EnrichmentPanel({ contact }: { contact: Contact }) {
  const [data, setData] = useState<EnrichmentSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [starting, setStarting] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // promoting = id of the contact currently being promoted, "all" for the
  // bulk button, or null for idle. Used to disable buttons during in-flight
  // requests so a double-click can't fire two promotes.
  const [promoting, setPromoting] = useState<string | null>(null);

  async function promoteOne(id: string) {
    setPromoting(id);
    setErr(null);
    try {
      await api.promoteContact(id);
      await load();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setPromoting(null);
    }
  }

  async function promoteAll(ids: string[]) {
    if (ids.length === 0) return;
    setPromoting("all");
    setErr(null);
    try {
      await api.bulkPromoteContacts(ids);
      await load();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setPromoting(null);
    }
  }

  async function load() {
    setLoading(true);
    setErr(null);
    try {
      setData(await api.getEnrichments(contact.id));
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contact.id]);

  // Poll while a run is still active so the operator sees completion
  // without manually refreshing.
  useEffect(() => {
    const pending = data?.runs.some(
      (r) => r.status === "pending" || r.status === "running",
    );
    if (!pending) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data?.runs.map((r) => r.status).join(",")]);

  async function startEnrich() {
    setStarting(true);
    setErr(null);
    try {
      await api.enrichContact(contact.id);
      await load();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setStarting(false);
    }
  }

  const reachable = Boolean(contact.email || contact.phone);
  const activeRun = data?.runs.find(
    (r) => r.status === "pending" || r.status === "running",
  );
  const completedRuns =
    data?.runs.filter((r) => r.status === "completed").length ?? 0;
  const failedRuns =
    data?.runs.filter(
      (r) => r.status === "failed" || r.status === "over_budget",
    ).length ?? 0;
  const totalRuns = data?.runs.length ?? 0;
  // Did any enrichment run surface NEW contact info for THIS person
  // (not just other people at the same org)? We match by normalized
  // name and treat "new info" as an email or phone the contact didn't
  // already have. If we find a same-name suggestion that's actually a
  // different person, the worst outcome is a false positive "found" —
  // Morgan verifies before clicking "Add to queue" anyway.
  const selfMatchSuggestion = data?.suggested_contacts.find(
    (s) => normalizeName(s.name) === normalizeName(contact.name),
  );
  const newEmail =
    selfMatchSuggestion?.email && !contact.email
      ? selfMatchSuggestion.email
      : null;
  const newPhone =
    selfMatchSuggestion?.phone && !contact.phone
      ? selfMatchSuggestion.phone
      : null;
  const foundNewInfoForSelf = Boolean(newEmail || newPhone);
  // "Run is done and we can give a verdict" iff there's data, no run is
  // active, and at least one completed run exists. Until then we keep
  // the in-progress label so we don't tell Morgan "nothing found" while
  // a run is still crunching.
  const showVerdict =
    data !== null &&
    !activeRun &&
    data.runs.some((r) => r.status === "completed");
  // All runs failed (no completed, no in-progress) — surface separately
  // since the verdict box won't fire and the operator deserves a clear
  // failure message rather than just badges in the runs list.
  const allFailed =
    data !== null &&
    !activeRun &&
    totalRuns > 0 &&
    completedRuns === 0 &&
    failedRuns > 0;
  // Aggregate progress for the in-progress banner.
  const totalPagesSoFar =
    data?.runs.reduce((sum, r) => sum + r.pages_crawled, 0) ?? 0;
  const totalContactsSoFar =
    data?.runs.reduce((sum, r) => sum + r.contacts_extracted, 0) ?? 0;

  return (
    <div className="mt-4 pt-4 border-t">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2 flex-wrap">
          <h4 className="text-sm font-semibold">Find more contact info</h4>
          {totalRuns > 0 && (
            <EnrichmentStatusPill
              activeRun={Boolean(activeRun)}
              completedRuns={completedRuns}
              failedRuns={failedRuns}
              totalRuns={totalRuns}
            />
          )}
        </div>
        <Button
          variant="secondary"
          onClick={startEnrich}
          disabled={starting || Boolean(activeRun)}
          title={
            reachable
              ? "This contact already has an email or phone. Enrichment will still re-crawl the org domain looking for additional info."
              : "Crawl the contact's organization website looking for an email or phone for this person."
          }
        >
          {starting
            ? "Starting…"
            : activeRun
              ? "Enrichment in progress…"
              : "Find more contact info"}
        </Button>
      </div>
      {err && <p className="text-sm text-red-700 mt-2">{err}</p>}
      {loading && !data && (
        <p className="text-xs text-neutral-500 mt-2">Loading enrichment history…</p>
      )}
      {activeRun && (
        <div className="mt-3 rounded border border-blue-200 bg-blue-50 px-3 py-2 text-sm text-blue-900 flex items-center gap-3">
          <span
            className="inline-block h-2.5 w-2.5 rounded-full bg-blue-500 animate-pulse shrink-0"
            aria-hidden="true"
          />
          <div>
            <div className="font-medium">Enrichment in progress…</div>
            <div className="text-xs text-blue-800">
              {totalPagesSoFar} page{totalPagesSoFar === 1 ? "" : "s"} crawled
              {totalContactsSoFar > 0 && (
                <> · {totalContactsSoFar} contact{totalContactsSoFar === 1 ? "" : "s"} extracted</>
              )}
              {" · "}refreshing every 3s
            </div>
          </div>
        </div>
      )}
      {allFailed && (
        <div className="mt-3 rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-900">
          <strong>
            Enrichment failed
            {failedRuns > 1 ? ` (${failedRuns} runs)` : ""}.
          </strong>{" "}
          See per-run details below. You can click &ldquo;Find more contact
          info&rdquo; again to retry.
        </div>
      )}
      {showVerdict && (
        <div
          className={
            "mt-3 rounded border px-3 py-2 text-sm " +
            (foundNewInfoForSelf
              ? "border-brand-green/40 bg-brand-green-light/15 text-brand-green"
              : "border-amber-300 bg-amber-50 text-amber-900")
          }
        >
          {foundNewInfoForSelf ? (
            <>
              <strong>Found new contact info for {contact.name}:</strong>
              <ul className="mt-1 ml-4 list-disc">
                {newEmail && (
                  <li>
                    <span className="text-neutral-600">email:</span>{" "}
                    <code className="font-mono">{newEmail}</code>
                  </li>
                )}
                {newPhone && (
                  <li>
                    <span className="text-neutral-600">phone:</span>{" "}
                    <code className="font-mono">{newPhone}</code>
                  </li>
                )}
              </ul>
              <p className="mt-1 text-xs text-neutral-700">
                Apply by editing {contact.name} above (suggestions are not
                auto-merged), or click &ldquo;Add to queue&rdquo; on the
                matching row below to bring it in as a standalone contact.
              </p>
            </>
          ) : (
            <>
              <strong>
                No new contact info found for {contact.name}.
              </strong>
              {data.suggested_contacts.length > 0 && (
                <p className="mt-1 text-xs">
                  The crawl did surface{" "}
                  <strong>{data.suggested_contacts.length}</strong> other{" "}
                  {data.suggested_contacts.length === 1 ? "contact" : "contacts"}{" "}
                  at the same organization (see suggestions below) — some may
                  still be useful even though they&apos;re not {contact.name}.
                </p>
              )}
            </>
          )}
        </div>
      )}
      {data && data.runs.length > 0 && (
        <div className="mt-3 space-y-2">
          <p className="text-xs text-neutral-600">
            {data.runs.length} enrichment{" "}
            {data.runs.length === 1 ? "run" : "runs"}
            {data.suggested_contacts.length > 0 && (
              <>
                {" · "}
                <strong>{data.suggested_contacts.length}</strong> suggested{" "}
                {data.suggested_contacts.length === 1 ? "contact" : "contacts"}
              </>
            )}
          </p>
          {data.runs.map((r) => {
            const crawledSet = new Set(r.crawled_pages.map((p) => p.url));
            const formReachable = r.crawled_pages.some((p) => p.has_contact_form);
            const formMailtos = Array.from(
              new Set(
                r.crawled_pages.flatMap((p) =>
                  (p.contact_form_actions || [])
                    .filter((a) => a.toLowerCase().startsWith("mailto:"))
                    .map((a) => a.replace(/^mailto:/i, "")),
                ),
              ),
            );
            return (
              <div
                key={r.id}
                className="text-xs border rounded p-3 bg-neutral-50 space-y-2"
              >
                <div className="flex items-center gap-2 text-neutral-700">
                  <Badge
                    intent={
                      r.status === "completed"
                        ? "ok"
                        : r.status === "failed" || r.status === "over_budget"
                          ? "bad"
                          : "info"
                    }
                  >
                    {r.status}
                  </Badge>
                  <span>
                    {r.pages_crawled} pages · {r.contacts_extracted} contacts ·
                    {" "}
                    {(r.cost_cents / 100).toFixed(2)}¢
                  </span>
                  {r.seed_source && (
                    <span title="How this enrichment's seed URL was chosen">
                      <Badge intent="neutral">
                        seed: {seedSourceLabel(r.seed_source)}
                      </Badge>
                    </span>
                  )}
                  {formReachable && (
                    <span title="At least one crawled page exposes a contact form — reachable even without a published email">
                      <Badge intent="info">form-reachable</Badge>
                    </span>
                  )}
                </div>
                {formMailtos.length > 0 && (
                  <p className="text-[11px] text-neutral-700">
                    Form action reveals:{" "}
                    {formMailtos.map((addr, i) => (
                      <span key={addr}>
                        {i > 0 && ", "}
                        <code className="font-mono bg-white px-1 py-0.5 rounded border">
                          {addr}
                        </code>
                      </span>
                    ))}
                  </p>
                )}

                {r.serp_attempts.length > 0 && (
                  <div className="space-y-1">
                    {r.serp_attempts.map((a, i) => (
                      <div key={i} className="space-y-0.5">
                        <div className="text-neutral-600">
                          <span className="font-medium">SERP {a.provider || "—"}:</span>{" "}
                          <code className="font-mono text-[11px] bg-white px-1 py-0.5 rounded border">
                            {a.query || "(empty)"}
                          </code>
                          {a.note && (
                            <span className="text-neutral-500 ml-2">
                              ({a.note})
                            </span>
                          )}
                        </div>
                        {a.urls.length > 0 ? (
                          <ul className="ml-4 space-y-0.5">
                            {a.urls.slice(0, 5).map((u) => (
                              <li key={u} className="flex items-baseline gap-2">
                                <Badge
                                  intent={crawledSet.has(u) ? "ok" : "neutral"}
                                >
                                  {crawledSet.has(u) ? "crawled" : "skipped"}
                                </Badge>
                                <a
                                  href={u}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="break-all text-brand-blue hover:underline"
                                >
                                  {u}
                                </a>
                              </li>
                            ))}
                            {a.urls.length > 5 && (
                              <li className="text-neutral-500">
                                …{a.urls.length - 5} more
                              </li>
                            )}
                          </ul>
                        ) : (
                          <p className="ml-4 text-neutral-500 italic">
                            no results
                          </p>
                        )}
                      </div>
                    ))}
                  </div>
                )}

                {r.crawled_pages.length > 0 && (
                  <details className="text-neutral-700">
                    <summary className="cursor-pointer text-neutral-600">
                      Crawled pages ({r.crawled_pages.length})
                    </summary>
                    <ul className="mt-1 ml-4 space-y-0.5">
                      {r.crawled_pages.map((p) => {
                        const mailtoAction = p.contact_form_actions?.find((a) =>
                          a.toLowerCase().startsWith("mailto:"),
                        );
                        return (
                          <li key={p.url} className="flex items-baseline gap-2 flex-wrap">
                            <Badge
                              intent={
                                p.status === "fetched"
                                  ? "ok"
                                  : p.status === "error" ||
                                      p.status.startsWith("blocked")
                                    ? "bad"
                                    : "warn"
                              }
                            >
                              {p.status}
                              {p.http_status && ` ${p.http_status}`}
                            </Badge>
                            {p.has_contact_form && (
                              <span title="This page contains a contact-style form (reachable via form even when no email is published)">
                                <Badge intent="info">contact form</Badge>
                              </span>
                            )}
                            <a
                              href={p.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="break-all text-brand-blue hover:underline"
                            >
                              {p.url}
                            </a>
                            {mailtoAction && (
                              <span
                                className="text-[11px] text-neutral-700"
                                title="The contact form's action revealed a mailto address"
                              >
                                form →{" "}
                                <code className="font-mono bg-white px-1 py-0.5 rounded border">
                                  {mailtoAction.replace(/^mailto:/i, "")}
                                </code>
                              </span>
                            )}
                            {p.error && (
                              <span className="text-red-700 text-[11px]">
                                {p.error}
                              </span>
                            )}
                          </li>
                        );
                      })}
                    </ul>
                  </details>
                )}
              </div>
            );
          })}
        </div>
      )}
      {data && data.suggested_contacts.length > 0 && (
        <div className="mt-3 space-y-2">
          <div className="flex items-center justify-between gap-2 flex-wrap">
            <p className="text-xs font-medium text-neutral-700">
              Suggestions found ({data.suggested_contacts.length}):
            </p>
            <Button
              variant="secondary"
              onClick={() => promoteAll(data.suggested_contacts.map((c) => c.id))}
              disabled={promoting !== null}
              title="Move every suggestion into the main review queue as a standalone contact. They keep their extracted email/phone and will be reachable via the standard filters."
            >
              {promoting === "all"
                ? "Adding…"
                : `Add all ${data.suggested_contacts.length} to queue`}
            </Button>
          </div>
          {data.suggested_contacts.map((c) => (
            <div
              key={c.id}
              className="text-sm border rounded p-2 bg-neutral-50 flex items-start justify-between gap-2"
            >
              <div className="min-w-0 flex-1">
                <div className="font-medium">{c.name}</div>
                {c.title && (
                  <div className="text-xs text-neutral-600">{c.title}</div>
                )}
                <div className="text-xs mt-1 flex flex-wrap gap-x-3">
                  {c.email && (
                    <span>
                      <span className="text-neutral-500">email:</span> {c.email}
                    </span>
                  )}
                  {c.phone && (
                    <span>
                      <span className="text-neutral-500">phone:</span> {c.phone}
                    </span>
                  )}
                  {!c.email && !c.phone && (
                    <span className="text-neutral-500">
                      no new contact info on this row
                    </span>
                  )}
                </div>
              </div>
              <Button
                variant="secondary"
                onClick={() => promoteOne(c.id)}
                disabled={promoting !== null}
                title="Move this suggestion into the main review queue as a standalone contact"
              >
                {promoting === c.id ? "Adding…" : "Add to queue"}
              </Button>
            </div>
          ))}
          <p className="text-xs text-neutral-500">
            &ldquo;Add to queue&rdquo; turns a suggestion into a standalone
            contact in /review. Suggestions you don&apos;t add stay attached to
            this contact and can be picked up later.
          </p>
        </div>
      )}
      {data && data.runs.length === 0 && !loading && (
        <p className="text-xs text-neutral-500 mt-2">
          No enrichment runs yet. Click the button to start one.
        </p>
      )}
    </div>
  );
}

function csvEscape(v: unknown): string {
  if (v === null || v === undefined) return "";
  const s = String(v);
  if (/[",\r\n]/.test(s)) return '"' + s.replace(/"/g, '""') + '"';
  return s;
}

function buildCsv(rows: Contact[]): string {
  const headers = [
    "name",
    "title",
    "email",
    "alt_email",
    "email_confidence",
    "phone",
    "segment_tag",
    "review_status",
    "dedupe_status",
    "evidence_verified",
    "evidence_excerpt",
    "source_page_url",
    "run_id",
    "created_at",
  ];
  const lines = [headers.join(",")];
  for (const r of rows) {
    lines.push(
      [
        r.name,
        r.title,
        r.email,
        r.alt_email,
        r.email_confidence,
        r.phone,
        r.segment_tag,
        r.review_status,
        r.dedupe_status,
        r.evidence_verified,
        r.evidence_excerpt,
        r.source_page_url,
        r.run_id,
        r.created_at,
      ]
        .map(csvEscape)
        .join(","),
    );
  }
  return lines.join("\r\n");
}

function downloadCsv(csv: string, filename: string): void {
  const blob = new Blob(["\uFEFF" + csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}


function ScoreCell({ contact }: { contact: Contact }) {
  const signals = [
    {
      key: "role",
      label: "Role match",
      value: contact.score_role_match,
      explain:
        "Title looks like a role we sell to (band director, theater director, head of school, etc.) — not a student/parent/admin assistant.",
    },
    {
      key: "state",
      label: "State match",
      value: contact.score_state_match,
      explain:
        "Organization state is on the target-state list for outbound. Off means the org is outside our priority territory.",
    },
    {
      key: "direct_email",
      label: "Direct email",
      value: contact.score_has_direct_email,
      explain:
        "A real email address was extracted from the page (not a pattern-guess that still needs BDR verification).",
    },
    {
      key: "phone",
      label: "Phone",
      value: contact.score_has_phone,
      explain: "A phone number was extracted alongside the contact.",
    },
    {
      key: "travel_signal",
      label: "Travel signal",
      value: contact.score_has_travel_signal,
      explain:
        "The source page mentions tours, trips, performance travel, exchange programs, or similar \"this person plans travel\" hints.",
    },
  ];
  const total = signals.filter((s) => s.value).length;
  // Tooltip text — readable in the native title bubble even if we add a
  // fancier popover later.
  const tip = signals
    .map((s) => `${s.value ? "\u2713" : "\u2717"} ${s.label} — ${s.explain}`)
    .join("\n\n");
  return (
    <div className="inline-flex items-center gap-2" title={tip}>
      <span className="font-medium tabular-nums">{total}/5</span>
      <div className="flex gap-0.5">
        {signals.map((s) => (
          <span
            key={s.key}
            className={`inline-block h-2 w-2 rounded-full ${
              s.value ? "bg-emerald-500" : "bg-neutral-200"
            }`}
            title={`${s.label}: ${s.value ? "yes" : "no"} — ${s.explain}`}
            aria-label={`${s.label}: ${s.value ? "yes" : "no"}`}
          />
        ))}
      </div>
    </div>
  );
}
