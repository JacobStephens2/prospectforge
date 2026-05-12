"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, type Source } from "@/api/client";
import { Button, Card, Input, Label, Select } from "@/components/ui";

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
          </div>
          <div className="md:col-span-2">
            <Button type="submit">Create source</Button>
            {error && <p className="text-red-700 text-sm mt-2">{error}</p>}
          </div>
        </form>
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
