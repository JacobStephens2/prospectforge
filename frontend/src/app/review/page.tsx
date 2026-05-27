"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
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
  const initialRun = params.get("run_id") ?? "";
  const initialStatus = params.get("review_status") ?? "pending";

  const [runId, setRunId] = useState(initialRun);
  const [reviewStatus, setReviewStatus] = useState(initialStatus);
  const [dedupeStatus, setDedupeStatus] = useState("");
  // "reachable" = has email OR phone. Default on — most of the time you
  // don't want to see contacts you can't actually reach.
  const [contactInfo, setContactInfo] = useState<"any" | "reachable" | "none">("reachable");
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [sorting, setSorting] = useState<SortingState>([]);
  const [selected, setSelected] = useState<Contact | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

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

  async function load() {
    const rows = await api.listContacts({
      run_id: runId || undefined,
      review_status: reviewStatus || undefined,
      dedupe_status: dedupeStatus || undefined,
      has_contact_info:
        contactInfo === "reachable" ? true : contactInfo === "none" ? false : undefined,
      limit: 200,
    });
    setContacts(rows);
  }
  useEffect(() => {
    load();
  }, [runId, reviewStatus, dedupeStatus, contactInfo]);

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
        <div className="flex items-baseline gap-3">
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
        </div>
        <div className="flex items-center gap-2 flex-wrap">
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
        </div>
      </div>
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
          <div className="flex items-center justify-between">
            <h3 id="contact-detail-title">{selected.name}</h3>
            <Button variant="ghost" onClick={() => setSelected(null)}>
              Close
            </Button>
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

  return (
    <div className="mt-4 pt-4 border-t">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <h4 className="text-sm font-semibold">Find more contact info</h4>
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
          {data.runs.map((r) => (
            <div
              key={r.id}
              className="text-xs flex items-center gap-2 text-neutral-700"
            >
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
            </div>
          ))}
        </div>
      )}
      {data && data.suggested_contacts.length > 0 && (
        <div className="mt-3 space-y-2">
          <p className="text-xs font-medium text-neutral-700">
            Suggestions found:
          </p>
          {data.suggested_contacts.map((c) => (
            <div
              key={c.id}
              className="text-sm border rounded p-2 bg-neutral-50"
            >
              <div className="font-medium">{c.name}</div>
              {c.title && <div className="text-xs text-neutral-600">{c.title}</div>}
              <div className="text-xs mt-1">
                {c.email && (
                  <span className="mr-3">
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
          ))}
          <p className="text-xs text-neutral-500">
            Apply useful info manually by editing this contact (above) — the
            suggestions are not auto-merged.
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
