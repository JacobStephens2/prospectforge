"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { api, type Source } from "@/api/client";
import { Button, Card, Input, Label, Select } from "@/components/ui";

export default function NewRunPage() {
  return (
    <Suspense fallback={<p>Loading…</p>}>
      <NewRunForm />
    </Suspense>
  );
}

function NewRunForm() {
  const router = useRouter();
  const params = useSearchParams();
  const preselect = params.get("source") ?? "";

  const [sources, setSources] = useState<Source[]>([]);
  const [sourceId, setSourceId] = useState(preselect);
  const [maxCost, setMaxCost] = useState("200");
  const [approved, setApproved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.listSources().then(setSources).catch((e) => setError((e as Error).message));
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!approved) {
      setError("Approve the budget cap to launch a run.");
      return;
    }
    setError(null);
    setBusy(true);
    try {
      const run = await api.createRun({
        source_id: sourceId,
        max_cost_cents: Number(maxCost),
      });
      router.push(`/runs/${run.id}`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <h1>New run</h1>
      <Card>
        <form onSubmit={submit} className="space-y-4">
          <div>
            <Label htmlFor="source">Source</Label>
            <Select
              id="source"
              value={sourceId}
              onChange={(e) => setSourceId(e.target.value)}
              required
              className="w-full"
            >
              <option value="" disabled>
                Pick a source…
              </option>
              {sources.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name} — {s.url}
                </option>
              ))}
            </Select>
          </div>

          <div>
            <Label htmlFor="cost">Budget cap (cents)</Label>
            <Input
              id="cost"
              type="number"
              min={1}
              max={100000}
              value={maxCost}
              onChange={(e) => setMaxCost(e.target.value)}
              required
            />
            <p className="text-xs text-neutral-600 mt-1">
              Hard cap; the extraction worker halts the run if cumulative spend
              would exceed this. ~1¢/page at current Sonnet 4.5 pricing with
              prompt caching warm.
            </p>
          </div>

          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={approved}
              onChange={(e) => setApproved(e.target.checked)}
              className="h-4 w-4"
            />
            <span>
              I approve spending up to <strong>{maxCost}¢</strong> on this run.
            </span>
          </label>

          <Button type="submit" disabled={busy || !sourceId || !approved}>
            {busy ? "Creating…" : "Create run"}
          </Button>
          {error && <p className="text-red-700 text-sm">{error}</p>}
        </form>
      </Card>
    </div>
  );
}
