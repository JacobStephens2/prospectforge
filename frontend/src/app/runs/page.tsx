"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, type Run } from "@/api/client";
import { Card } from "@/components/ui/card";
import { Select } from "@/components/NativeSelect";
import { Badge } from "@/components/Badge";

const statuses = ["", "pending", "running", "completed", "failed", "over_budget"];

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

  async function load() {
    setRuns(await api.listRuns(filter || undefined));
  }
  useEffect(() => {
    load();
  }, [filter]);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1>Runs</h1>
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

      <Card>
        {runs.length === 0 && (
          <p className="text-sm text-neutral-600">No runs match.</p>
        )}
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-neutral-500 uppercase">
              <th className="py-2">Run</th>
              <th>Source</th>
              <th>Tier</th>
              <th>Status</th>
              <th>Pages</th>
              <th>Contacts</th>
              <th>Cost / Cap</th>
              <th>Created</th>
              <th>Finished</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {runs.map((r) => (
              <tr key={r.id} className="hover:bg-neutral-50">
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
                <td>{r.contacts_extracted}</td>
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
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
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
