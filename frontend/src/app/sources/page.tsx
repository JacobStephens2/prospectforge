"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, type Source } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/NativeSelect";
import { Badge } from "@/components/Badge";

export default function SourcesPage() {
  const [sources, setSources] = useState<Source[]>([]);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [segment, setSegment] = useState("");
  const [tier, setTier] = useState("1");
  const [followExternal, setFollowExternal] = useState(false);
  const [maxExternal, setMaxExternal] = useState("20");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [created, setCreated] = useState<Source | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState<Partial<Source>>({});
  const [editBusy, setEditBusy] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);
  // Default to active-only so Delete actually removes the row from view.
  // Operator can flip this to recover a paused/deleted source.
  const [showInactive, setShowInactive] = useState(false);

  function startEdit(s: Source) {
    setEditError(null);
    setEditingId(s.id);
    setEditDraft({
      name: s.name,
      url: s.url,
      segment_template: s.segment_template,
      crawl_tier: s.crawl_tier,
      follow_external_links: s.follow_external_links,
      max_external_pages: s.max_external_pages,
      active: s.active,
    });
  }

  function cancelEdit() {
    setEditingId(null);
    setEditDraft({});
    setEditError(null);
  }

  async function saveEdit(s: Source) {
    if (editDraft.url && editDraft.url !== s.url) {
      if (
        !confirm(
          `You changed the seed URL of "${s.name}" from\n  ${s.url}\nto\n  ${editDraft.url}\n\nThis effectively makes it a different source. Past runs still point at the old URL but the next run will crawl the new one. Proceed?`,
        )
      ) {
        return;
      }
    }
    setEditError(null);
    setEditBusy(true);
    try {
      await api.updateSource(s.id, editDraft);
      cancelEdit();
      load();
    } catch (e) {
      setEditError((e as Error).message);
    } finally {
      setEditBusy(false);
    }
  }

  async function load() {
    try {
      setSources(await api.listSources({ activeOnly: !showInactive }));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  useEffect(() => {
    load();
    // load() depends on showInactive — re-fetch when the toggle flips.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showInactive]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setCreated(null);
    setSubmitting(true);
    try {
      const newSource = await api.createSource({
        name,
        url,
        segment_template: segment || null,
        crawl_tier: Number(tier),
        follow_external_links: followExternal,
        max_external_pages: Number(maxExternal),
      });
      setName("");
      setUrl("");
      setSegment("");
      setCreated(newSource);
      load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSubmitting(false);
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
          <div className="md:col-span-2 border-t pt-4 space-y-2">
            <label className="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                checked={followExternal}
                onChange={(e) => setFollowExternal(e.target.checked)}
                className="mt-0.5 h-4 w-4"
              />
              <span>
                <span className="font-medium">Follow external links</span>
                <span className="block text-xs text-muted-foreground mt-0.5">
                  Enable for directory-style sources where the actual contacts
                  live on each listed member&apos;s own site (e.g. Northwind / Global
                  Learning, TTA). The crawler will follow off-domain
                  <code className="mx-1">&lt;a href&gt;</code>links on the seed page
                  up to the cap below, subject to the same SSRF guard and rate
                  limit. Off for single-page directories where every contact is
                  on the seed itself.
                </span>
              </span>
            </label>
            {followExternal && (
              <div className="pl-6">
                <Label htmlFor="maxext">Max external pages</Label>
                <Input
                  id="maxext"
                  type="number"
                  min={0}
                  max={1000}
                  value={maxExternal}
                  onChange={(e) => setMaxExternal(e.target.value)}
                  className="w-32"
                />
                <p className="text-xs text-muted-foreground mt-1">
                  Hard cap on off-domain pages this source will crawl per run.
                  At ~1¢/page, 20 ≈ 20¢; 100 ≈ $1.00; 1000 ≈ $10.00.
                </p>
              </div>
            )}
          </div>
          <div className="md:col-span-2">
            <Button type="submit" disabled={submitting}>
              {submitting ? "Creating…" : "Create source"}
            </Button>
            {error && (
              <div
                role="alert"
                className="mt-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800"
              >
                <span className="font-medium">Couldn&apos;t create source:</span>{" "}
                {error}
              </div>
            )}
            {created && (
              <div
                role="status"
                className="mt-2 rounded-md border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-900 flex flex-wrap items-center gap-x-3 gap-y-1"
              >
                <span>
                  <span className="font-medium">Source created:</span>{" "}
                  {created.name}. It&apos;s ready to crawl — kick off a run
                  whenever you&apos;re ready.
                </span>
                <Link
                  href={`/runs/new?source=${created.id}`}
                  className="underline font-medium"
                >
                  Start a run →
                </Link>
                <button
                  type="button"
                  onClick={() => setCreated(null)}
                  className="ml-auto text-xs text-green-800 underline"
                >
                  Dismiss
                </button>
              </div>
            )}
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
                A self-hosted headless browser service (Playwright under the
                hood). Use this for sources where Tier 1 returns an empty
                or mostly-empty page — typically a JavaScript-rendered SPA,
                a member-directory widget, or a site that requires session
                cookies. Slower and resource-heavier than Tier 1, so opt in
                per source rather than as a default. Firecrawl <code>/map</code>
                is also used for URL discovery on Tier-2 sources, which
                often surfaces directory links that a static-HTML parse
                doesn&apos;t see.
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
        <div className="flex items-center justify-between gap-3">
          <h3>
            {showInactive ? "All sources" : "Active sources"} ({sources.length})
          </h3>
          <label className="flex items-center gap-2 text-sm text-neutral-700">
            <input
              type="checkbox"
              checked={showInactive}
              onChange={(e) => setShowInactive(e.target.checked)}
              className="h-4 w-4"
            />
            Show inactive
          </label>
        </div>
        <div className="mt-3 divide-y">
          {sources.length === 0 && (
            <p className="text-sm text-neutral-600 py-2">
              {showInactive
                ? "No sources yet."
                : "No active sources. Toggle 'Show inactive' to see deleted/paused sources."}
            </p>
          )}
          {sources.map((s) =>
            editingId === s.id ? (
              <div key={s.id} className="py-3 space-y-3 bg-amber-50/40 -mx-4 px-4">
                <div className="text-xs uppercase tracking-wide text-amber-900 font-medium">
                  Editing source
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  <div>
                    <Label htmlFor={`edit-name-${s.id}`}>Name</Label>
                    <Input
                      id={`edit-name-${s.id}`}
                      value={editDraft.name ?? ""}
                      onChange={(e) =>
                        setEditDraft((d) => ({ ...d, name: e.target.value }))
                      }
                    />
                  </div>
                  <div>
                    <Label htmlFor={`edit-url-${s.id}`}>URL</Label>
                    <Input
                      id={`edit-url-${s.id}`}
                      type="url"
                      value={editDraft.url ?? ""}
                      onChange={(e) =>
                        setEditDraft((d) => ({ ...d, url: e.target.value }))
                      }
                    />
                  </div>
                  <div>
                    <Label htmlFor={`edit-seg-${s.id}`}>Segment template</Label>
                    <Input
                      id={`edit-seg-${s.id}`}
                      value={editDraft.segment_template ?? ""}
                      onChange={(e) =>
                        setEditDraft((d) => ({
                          ...d,
                          segment_template: e.target.value || null,
                        }))
                      }
                    />
                  </div>
                  <div>
                    <Label htmlFor={`edit-tier-${s.id}`}>Crawl tier</Label>
                    <Select
                      id={`edit-tier-${s.id}`}
                      value={String(editDraft.crawl_tier ?? 1)}
                      onChange={(e) =>
                        setEditDraft((d) => ({
                          ...d,
                          crawl_tier: Number(e.target.value),
                        }))
                      }
                    >
                      <option value="1">1 — httpx + trafilatura</option>
                      <option value="2">2 — Firecrawl</option>
                    </Select>
                  </div>
                  <div className="md:col-span-2 space-y-2 border-t pt-3">
                    <label className="flex items-start gap-2 text-sm">
                      <input
                        type="checkbox"
                        checked={editDraft.follow_external_links ?? false}
                        onChange={(e) =>
                          setEditDraft((d) => ({
                            ...d,
                            follow_external_links: e.target.checked,
                          }))
                        }
                        className="mt-0.5 h-4 w-4"
                      />
                      <span>
                        <span className="font-medium">Follow external links</span>
                        <span className="block text-xs text-muted-foreground mt-0.5">
                          Crawl off-domain pages linked from the seed (member
                          directory style). Capped by &ldquo;max external
                          pages&rdquo;.
                        </span>
                      </span>
                    </label>
                    {editDraft.follow_external_links && (
                      <div className="pl-6">
                        <Label htmlFor={`edit-maxext-${s.id}`}>
                          Max external pages
                        </Label>
                        <Input
                          id={`edit-maxext-${s.id}`}
                          type="number"
                          min={0}
                          max={1000}
                          value={editDraft.max_external_pages ?? 20}
                          onChange={(e) =>
                            setEditDraft((d) => ({
                              ...d,
                              max_external_pages: Number(e.target.value),
                            }))
                          }
                          className="w-32"
                        />
                      </div>
                    )}
                    <label className="flex items-start gap-2 text-sm pt-1">
                      <input
                        type="checkbox"
                        checked={editDraft.active ?? true}
                        onChange={(e) =>
                          setEditDraft((d) => ({ ...d, active: e.target.checked }))
                        }
                        className="mt-0.5 h-4 w-4"
                      />
                      <span>
                        <span className="font-medium">Active</span>
                        <span className="block text-xs text-muted-foreground mt-0.5">
                          Inactive sources are hidden from the default list.
                          Uncheck to pause without deleting; past runs stay
                          intact either way.
                        </span>
                      </span>
                    </label>
                  </div>
                </div>
                <div className="flex gap-2 items-center">
                  <Button onClick={() => saveEdit(s)} disabled={editBusy}>
                    {editBusy ? "Saving…" : "Save changes"}
                  </Button>
                  <Button variant="secondary" onClick={cancelEdit} disabled={editBusy}>
                    Cancel
                  </Button>
                  {editError && (
                    <span className="text-sm text-red-700">{editError}</span>
                  )}
                </div>
              </div>
            ) : (
              <div key={s.id} className="py-2 flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-medium truncate">{s.name}</span>
                    <span title={tierTitle(s.crawl_tier)}>
                      <Badge intent={tierIntent(s.crawl_tier)}>
                        Tier {s.crawl_tier}
                      </Badge>
                    </span>
                    {!s.active && (
                      <span title="Inactive — hidden from the default list">
                        <Badge intent="warn">inactive</Badge>
                      </span>
                    )}
                  </div>
                  <div className="text-xs text-neutral-600 truncate">{s.url}</div>
                </div>
                <div className="flex gap-2 shrink-0">
                  <Button variant="secondary" onClick={() => startEdit(s)}>
                    Edit
                  </Button>
                  <Link href={`/runs/new?source=${s.id}`}>
                    <Button variant="secondary">New run</Button>
                  </Link>
                  <Button
                    variant="destructive"
                    onClick={async () => {
                      if (
                        !confirm(
                          `Delete source "${s.name}"? It will be hidden from this list. Past runs and their contacts stay intact.`,
                        )
                      ) {
                        return;
                      }
                      try {
                        await api.deleteSource(s.id);
                        load();
                      } catch (e) {
                        setError((e as Error).message);
                      }
                    }}
                  >
                    Delete
                  </Button>
                </div>
              </div>
            ),
          )}
        </div>
      </Card>
    </div>
  );
}


function tierIntent(t: number): "ok" | "warn" | "bad" | "info" | "neutral" {
  // Same color mapping as /runs so the operator builds one mental model.
  if (t === 1) return "ok";
  if (t === 2) return "info";
  if (t === 3) return "warn";
  return "neutral";
}

function tierTitle(t: number): string {
  if (t === 1) return "Tier 1 — httpx + trafilatura (default, fastest)";
  if (t === 2) return "Tier 2 — Firecrawl (self-hosted, JS rendering)";
  if (t === 3) return "Tier 3 — Playwright fallback (deferred to v1.1)";
  return `Tier ${t}`;
}
