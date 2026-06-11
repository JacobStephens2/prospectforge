"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, type Run } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Select } from "@/components/NativeSelect";
import { Badge } from "@/components/Badge";

const statuses = ["", "pending", "running", "completed", "failed", "over_budget"];
const PAGE_SIZE = 50;

function tierIntent(t: number): "ok" | "warn" | "bad" | "info" | "neutral" {
  // Tier 1 = direct httpx (cheapest, default); tier 2 = Firecrawl
  // (paid, JS rendering); tier 3 = Playwright (heaviest). Color hints at
  // cost / complexity so operators can scan the list.
  if (t === 1) return "ok";
  if (t === 2) return "info";
  if (t === 3) return "warn";
  return "neutral";
}

function statusBadge(s: string) {
  switch (s) {
    case "completed":
      return <Badge intent="ok">{s}</Badge>;
    case "running":
      return <Badge intent="info">{s}</Badge>;
    case "failed":
    case "over_budget":
      return <Badge intent="bad">{s}</Badge>;
    case "pending":
      return <Badge intent="warn">{s}</Badge>;
    default:
      return <Badge>{s}</Badge>;
  }
}

export default function RunsPage() {
  const [runs, setRuns] = useState<Run[]>([]);
  const [filter, setFilter] = useState("");
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkError, setBulkError] = useState<string | null>(null);
  const [totalCount, setTotalCount] = useState<number | null>(null);
  const [statusCounts, setStatusCounts] = useState<Record<string, number>>({});
  const [page, setPage] = useState(0);

  async function load() {
    const [fresh, counts] = await Promise.all([
      api.listRuns(filter || undefined, PAGE_SIZE, page * PAGE_SIZE),
      api.runCounts(),
    ]);
    setRuns(fresh);
    setTotalCount(counts.total);
    setStatusCounts(counts.by_status);
    // Prune selections for rows that no longer exist (e.g. just deleted
    // or filtered out) so the bulk bar doesn't claim phantom rows.
    setSelectedIds((prev) => {
      const stillThere = new Set(fresh.map((r) => r.id));
      const pruned = new Set<string>();
      for (const id of prev) if (stillThere.has(id)) pruned.add(id);
      return pruned;
    });
  }
  useEffect(() => {
    load();
  }, [filter, page]);

  // A status filter change resets paging back to the first page so we never
  // land on an offset past the end of the (smaller) filtered population.
  useEffect(() => {
    setPage(0);
  }, [filter]);

  // Total rows for the *current* view: the whole table when unfiltered,
  // otherwise the by-status count for the active filter.
  const effectiveTotal = filter
    ? statusCounts[filter] ?? 0
    : totalCount ?? 0;
  const pageCount = Math.max(1, Math.ceil(effectiveTotal / PAGE_SIZE));
  const hasPrev = page > 0;
  const hasNext = page + 1 < pageCount;

  function isSelectable(r: Run): boolean {
    return r.status !== "running";
  }

  function toggleOne(id: string, on: boolean) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  function toggleAll(on: boolean) {
    setSelectedIds(() => {
      if (!on) return new Set();
      return new Set(runs.filter(isSelectable).map((r) => r.id));
    });
  }

  async function deleteSelected() {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;
    const msg =
      `Delete ${ids.length} run${ids.length === 1 ? "" : "s"}?

` +
      "This also deletes their source pages, contacts, exports, and archived HTML. Not undoable.";
    if (!window.confirm(msg)) return;
    setBulkError(null);
    setBulkBusy(true);
    // Sequential delete so the first failure is obvious and we don't
    // dogpile the backend with parallel CASCADE work for big runs.
    const failed: { id: string; message: string }[] = [];
    for (const id of ids) {
      try {
        await api.deleteRun(id);
      } catch (e) {
        failed.push({ id, message: (e as Error).message });
      }
    }
    if (failed.length > 0) {
      setBulkError(
        `Deleted ${ids.length - failed.length} of ${ids.length}. Failed: ` +
          failed.map((f) => `${f.id.slice(0, 8)}… (${f.message})`).join("; "),
      );
    }
    setBulkBusy(false);
    await load();
  }

  async function deleteRun(r: Run) {
    // Cheap, explicit, and reversible-by-rerunning. Skip a fancy dialog;
    // browser confirm is fine for an admin-only screen.
    const msg = `Delete run ${r.id.slice(0, 8)}… (${r.source_name ?? "?"}, ${r.contacts_extracted} contacts)?\n\nThis also deletes its source pages, contacts, exports, and archived HTML. Not undoable.`;
    if (!window.confirm(msg)) return;
    setDeletingId(r.id);
    try {
      await api.deleteRun(r.id);
      await load();
    } catch (e) {
      alert((e as Error).message);
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-baseline gap-3">
          <h1>Runs</h1>
          {totalCount !== null && (
            <span className="text-sm text-neutral-600">
              {filter ? (
                <>
                  Showing <strong>{runs.length}</strong>{" "}
                  <code className="text-xs">{filter}</code> of{" "}
                  <strong>{totalCount}</strong> total
                </>
              ) : (
                <>
                  <strong>{totalCount}</strong> total
                  {Object.keys(statusCounts).length > 1 && (
                    <span className="text-neutral-500">
                      {" "}
                      ({Object.entries(statusCounts)
                        .sort((a, b) => b[1] - a[1])
                        .map(([s, n]) => `${n} ${s}`)
                        .join(", ")})
                    </span>
                  )}
                </>
              )}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <span className="text-sm text-neutral-600">Filter status:</span>
          <Select value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="">(all)</option>
            {statuses.filter(Boolean).map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </Select>
        </div>
      </div>

      {selectedIds.size > 0 && (
        <div className="flex items-center gap-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm">
          <span className="font-medium text-amber-900">
            {selectedIds.size} selected
          </span>
          <Button
            variant="destructive"
            size="sm"
            onClick={deleteSelected}
            disabled={bulkBusy}
          >
            {bulkBusy ? "Deleting…" : `Delete ${selectedIds.size} run${selectedIds.size === 1 ? "" : "s"}`}
          </Button>
          <button
            type="button"
            onClick={() => toggleAll(false)}
            className="text-xs text-amber-900 underline"
          >
            Clear selection
          </button>
        </div>
      )}
      {bulkError && (
        <p className="text-sm text-red-700">{bulkError}</p>
      )}

      <Card>
        {runs.length === 0 && (
          <p className="text-sm text-neutral-600">No runs match.</p>
        )}
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-neutral-500 uppercase">
              <th className="py-2 px-3 w-8">
                <input
                  type="checkbox"
                  aria-label="Select all eligible runs"
                  className="h-4 w-4"
                  checked={
                    runs.filter(isSelectable).length > 0 &&
                    runs.filter(isSelectable).every((r) => selectedIds.has(r.id))
                  }
                  ref={(el) => {
                    if (!el) return;
                    const sel = runs.filter(isSelectable);
                    const allSelected = sel.length > 0 && sel.every((r) => selectedIds.has(r.id));
                    const someSelected = sel.some((r) => selectedIds.has(r.id));
                    el.indeterminate = someSelected && !allSelected;
                  }}
                  onChange={(e) => toggleAll(e.target.checked)}
                />
              </th>
              <th className="py-2">Run</th>
              <th>Source</th>
              <th>Tier</th>
              <th>Status</th>
              <th>Pages</th>
              <th>Contacts</th>
              <th>Cost / Cap</th>
              <th>Created</th>
              <th>Finished</th>
              <th aria-label="Actions"></th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {runs.map((r) => (
              <tr key={r.id} className={`hover:bg-neutral-50 ${selectedIds.has(r.id) ? "bg-amber-50/60" : ""}`}>
                <td className="py-2 px-3">
                  <input
                    type="checkbox"
                    aria-label={`Select run ${r.id.slice(0, 8)}`}
                    className="h-4 w-4"
                    checked={selectedIds.has(r.id)}
                    disabled={!isSelectable(r)}
                    title={isSelectable(r) ? "Select this run" : "Can't select a running run"}
                    onChange={(e) => toggleOne(r.id, e.target.checked)}
                  />
                </td>
                <td className="py-2">
                  <Link href={`/runs/${r.id}`} className="font-mono text-xs">
                    {r.id.slice(0, 8)}…
                  </Link>
                </td>
                <td className="text-xs">{r.source_name ?? "—"}</td>
                <td>
                  {r.source_crawl_tier !== null ? (
                    <Badge intent={tierIntent(r.source_crawl_tier)}>
                      T{r.source_crawl_tier}
                    </Badge>
                  ) : (
                    "—"
                  )}
                </td>
                <td>{statusBadge(r.status)}</td>
                <td>{r.pages_crawled}</td>
                <td>
                  {r.contacts_extracted > 0 ? (
                    <Link
                      href={`/review?run_id=${r.id}`}
                      className="underline underline-offset-2"
                      title="Open the review queue for this run"
                    >
                      {r.contacts_extracted}
                    </Link>
                  ) : (
                    r.contacts_extracted
                  )}
                </td>
                <td>
                  <code>
                    {r.cost_cents}¢ / {r.max_cost_cents}¢
                  </code>
                </td>
                <td className="text-neutral-600 text-xs">
                  {new Date(r.created_at).toLocaleString()}
                </td>
                <td className="text-neutral-600 text-xs">
                  {r.completed_at ? (
                    <span title={`Started ${r.started_at ? new Date(r.started_at).toLocaleString() : "?"}`}>
                      {new Date(r.completed_at).toLocaleString()}
                      {r.started_at && (
                        <span className="text-neutral-400">
                          {" "}
                          ({runDuration(r.started_at, r.completed_at)})
                        </span>
                      )}
                    </span>
                  ) : r.status === "running" && r.started_at ? (
                    <span className="text-neutral-500">
                      running for {runDuration(r.started_at, new Date().toISOString())}
                    </span>
                  ) : (
                    "—"
                  )}
                </td>
                <td className="text-right">
                  <Button
                    variant="destructive"
                    size="sm"
                    disabled={deletingId !== null || r.status === "running"}
                    onClick={() => deleteRun(r)}
                    title={r.status === "running" ? "Wait for the run to finish" : "Delete this run"}
                  >
                    {deletingId === r.id ? "…" : "Delete"}
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      {effectiveTotal > PAGE_SIZE && (
        <div className="flex items-center justify-between text-sm">
          <span className="text-neutral-600">
            {effectiveTotal === 0 ? (
              "0 results"
            ) : (
              <>
                Showing <strong>{page * PAGE_SIZE + 1}</strong>–
                <strong>{Math.min((page + 1) * PAGE_SIZE, effectiveTotal)}</strong>{" "}
                of <strong>{effectiveTotal}</strong>
              </>
            )}
          </span>
          <div className="flex items-center gap-2">
            <Button
              variant="secondary"
              size="sm"
              disabled={!hasPrev}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
            >
              ← Prev
            </Button>
            <span className="text-neutral-600">
              Page {page + 1} of {pageCount}
            </span>
            <Button
              variant="secondary"
              size="sm"
              disabled={!hasNext}
              onClick={() => setPage((p) => p + 1)}
            >
              Next →
            </Button>
          </div>
        </div>
      )}
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
