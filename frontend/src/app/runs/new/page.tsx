"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { api, type Source } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/NativeSelect";

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

  const selectedSource = sources.find((s) => s.id === sourceId);
  const costCents = Number(maxCost);
  const costDollars = Number.isFinite(costCents)
    ? (costCents / 100).toLocaleString("en-US", {
        style: "currency",
        currency: "USD",
      })
    : "$0.00";

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
            {selectedSource && (
              <p className="text-xs text-neutral-600 mt-1">
                Tier {selectedSource.crawl_tier} —{" "}
                {selectedSource.crawl_tier === 2
                  ? "Firecrawl (headless browser)"
                  : "httpx + trafilatura"}
                {selectedSource.follow_external_links ? (
                  <>
                    {" "}· follows external links, up to{" "}
                    <strong>{selectedSource.max_external_pages}</strong> off-domain pages.
                  </>
                ) : (
                  <> · seed page only (no external link following).</>
                )}
              </p>
            )}
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
              Hard cap on Anthropic API spend. Only the contact-extraction
              step (Sonnet 4.5) costs money — ~1¢/page with prompt caching
              warm. Crawling is free regardless of tier (httpx and the
              self-hosted Firecrawl both run on this server), so the cap only
              bounds the LLM bill. The orchestrator halts the run as soon as
              cumulative extraction spend would exceed this.
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
              I approve spending up to{" "}
              <strong>
                {maxCost}¢ ({costDollars})
              </strong>{" "}
              on this run.
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
