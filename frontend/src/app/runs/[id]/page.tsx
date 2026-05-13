"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import { api, type Contact, type ExportSummary, type Run } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/Badge";

export default function RunDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [run, setRun] = useState<Run | null>(null);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastExport, setLastExport] = useState<ExportSummary | null>(null);

  async function loadAll() {
    const r = await api.getRun(id);
    setRun(r);
    const all = await api.listContacts({ run_id: id, limit: 500 });
    const by: Record<string, number> = {};
    for (const c of all) {
      by[c.review_status] = (by[c.review_status] ?? 0) + 1;
    }
    setCounts(by);
  }

  useEffect(() => {
    loadAll();
    // Light polling while running.
    const t = setInterval(loadAll, 3000);
    return () => clearInterval(t);
  }, [id]);

  async function start() {
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

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1>
          Run <code className="font-mono text-base">{run.id.slice(0, 8)}…</code>
        </h1>
        <div className="flex gap-2">
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

      <Card>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
          <Stat label="Status" value={run.status} />
          <Stat label="Pages crawled" value={String(run.pages_crawled)} />
          <Stat label="Contacts" value={String(run.contacts_extracted)} />
          <Stat
            label="Cost / cap"
            value={`${run.cost_cents}¢ / ${run.max_cost_cents}¢`}
          />
        </div>
      </Card>

      <Card>
        <h3>Review queue distribution</h3>
        <div className="mt-3 flex gap-2 flex-wrap">
          {Object.entries(counts).map(([status, n]) => (
            <Link key={status} href={`/review?run_id=${id}&review_status=${status}`}>
              <Badge intent={badgeIntent(status)}>
                {status}: {n}
              </Badge>
            </Link>
          ))}
          {Object.keys(counts).length === 0 && (
            <p className="text-sm text-neutral-600">No contacts yet.</p>
          )}
        </div>
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

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs uppercase text-neutral-500">{label}</div>
      <div className="text-base font-medium">{value}</div>
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
