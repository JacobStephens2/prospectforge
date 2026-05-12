"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, type Run } from "@/api/client";
import { Badge, Card, Select } from "@/components/ui";

const statuses = ["", "pending", "running", "completed", "failed", "over_budget"];

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
              <th>Status</th>
              <th>Pages</th>
              <th>Contacts</th>
              <th>Cost / Cap</th>
              <th>Created</th>
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
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
