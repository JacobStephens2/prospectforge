"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, type Source } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/NativeSelect";

export default function SourcesPage() {
  const [sources, setSources] = useState<Source[]>([]);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [segment, setSegment] = useState("");
  const [tier, setTier] = useState("1");
  const [error, setError] = useState<string | null>(null);

  async function load() {
    try {
      setSources(await api.listSources());
    } catch (e) {
      setError((e as Error).message);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api.createSource({
        name,
        url,
        segment_template: segment || null,
        crawl_tier: Number(tier),
      });
      setName("");
      setUrl("");
      setSegment("");
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <div className="space-y-6">
      <h1>Sources</h1>

      <Card>
        <h3>Add a source</h3>
        <form onSubmit={submit} className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-3">
          <div>
            <Label htmlFor="name">Name</Label>
            <Input
              id="name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Northwind Member Directory"
              required
            />
          </div>
          <div>
            <Label htmlFor="url">URL</Label>
            <Input
              id="url"
              type="url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://www.northwind-old.example/members"
              required
            />
          </div>
          <div>
            <Label htmlFor="segment">Segment template (optional)</Label>
            <Input
              id="segment"
              value={segment}
              onChange={(e) => setSegment(e.target.value)}
              placeholder="K-12 band directors"
            />
          </div>
          <div>
            <Label htmlFor="tier">Crawl tier</Label>
            <Select id="tier" value={tier} onChange={(e) => setTier(e.target.value)}>
              <option value="1">1 — httpx + trafilatura (default)</option>
              <option value="2">2 — Firecrawl (feature-flagged)</option>
            </Select>
            <p className="text-xs text-muted-foreground mt-1">
              Start with Tier 1 — try Tier 2 only if Tier 1 returns empty content.
            </p>
          </div>
          <div className="md:col-span-2">
            <Button type="submit">Create source</Button>
            {error && <p className="text-red-700 text-sm mt-2">{error}</p>}
          </div>
        </form>

        <details className="mt-4 text-sm text-muted-foreground">
          <summary className="cursor-pointer font-medium text-foreground">
            About crawl tiers
          </summary>
          <div className="mt-2 space-y-2 max-w-prose">
            <p>
              The crawler tries fetches in tiers. Each source is pinned to one
              tier so a flaky site doesn&apos;t fall back to a heavier path
              silently.
            </p>
            <ul className="list-disc pl-5 space-y-2">
              <li>
                <span className="font-semibold text-foreground">Tier 1 — httpx + trafilatura.</span>{" "}
                A plain HTTPS GET (Python <code>httpx</code>) followed by
                article extraction (<code>trafilatura</code>) to convert
                HTML into clean text. Fast, cheap, no headless browser.
                Works for almost every public directory, faculty page, or
                association listing where the contact data is in the
                server-rendered HTML. <strong>Use this by default.</strong>
              </li>
              <li>
                <span className="font-semibold text-foreground">Tier 2 — Firecrawl.</span>{" "}
                A self-hosted headless browser service. Use this only for
                sources where Tier 1 returns an empty or mostly-empty page
                — typically a JavaScript-rendered SPA, a Cloudflare-style
                challenge, or a site that requires session cookies. Slower
                and resource-heavier than Tier 1, so it&apos;s feature-flagged
                per source rather than always-on. <strong>Currently off by
                default for MVP</strong> — pilot sources should all work on
                Tier 1 per the build spec.
              </li>
              <li>
                <span className="font-semibold text-foreground">Tier 3 — Playwright fallback.</span>{" "}
                Deferred to v1.1. If both Tier 1 and Tier 2 fail and a source
                is important enough, we&apos;ll add an explicit Playwright
                path. Not selectable today.
              </li>
            </ul>
            <p>
              All tiers go through the same SSRF guard (no private IPs, no
              non-http schemes, redirects re-validated) and the same per-domain
              rate limit. Tier 2 doesn&apos;t bypass those — it just changes
              the rendering layer.
            </p>
          </div>
        </details>
      </Card>

      <Card>
        <h3>All sources ({sources.length})</h3>
        <div className="mt-3 divide-y">
          {sources.length === 0 && (
            <p className="text-sm text-neutral-600 py-2">No sources yet.</p>
          )}
          {sources.map((s) => (
            <div key={s.id} className="py-2 flex items-center justify-between">
              <div>
                <div className="font-medium">{s.name}</div>
                <div className="text-xs text-neutral-600">{s.url}</div>
              </div>
              <Link href={`/runs/new?source=${s.id}`}>
                <Button variant="secondary">New run</Button>
              </Link>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
