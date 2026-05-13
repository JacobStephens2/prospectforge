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
import { api, type Contact } from "@/api/client";
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
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [sorting, setSorting] = useState<SortingState>([]);
  const [selected, setSelected] = useState<Contact | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

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
      limit: 200,
    });
    setContacts(rows);
  }
  useEffect(() => {
    load();
  }, [runId, reviewStatus, dedupeStatus]);

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

  const cols = useMemo<ColumnDef<Contact>[]>(
    () => [
      {
        header: "Name",
        accessorKey: "name",
        cell: (c) => (
          <button
            className="text-left underline-offset-2 hover:underline"
            onClick={() => setSelected(c.row.original)}
          >
            {c.row.original.name}
          </button>
        ),
      },
      { header: "Title", accessorKey: "title" },
      { header: "Email", accessorKey: "email" },
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
        header: "Score",
        id: "score",
        accessorFn: (c) =>
          [
            c.score_role_match,
            c.score_state_match,
            c.score_has_direct_email,
            c.score_has_phone,
            c.score_has_travel_signal,
          ].filter(Boolean).length,
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
      <div className="flex items-center justify-between">
        <h1>Review queue</h1>
        <div className="flex items-center gap-2 flex-wrap">
          <Select value={reviewStatus} onChange={(e) => setReviewStatus(e.target.value)}>
            <option value="">(any review)</option>
            <option value="pending">pending</option>
            <option value="approved">approved</option>
            <option value="rejected">rejected</option>
            <option value="edited">edited</option>
            <option value="needs_review">needs_review</option>
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
                <Badge intent={selected.email_confidence === "verified" ? "ok" : "warn"}>
                  {selected.email_confidence}
                </Badge>
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
          </dl>
        </Card>
        </div>
      )}
    </div>
  );
}
