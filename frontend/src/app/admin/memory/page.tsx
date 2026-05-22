"use client";

import { useEffect, useState } from "react";
import { api, type MemoryResponse, type MemorySnapshot } from "@/api/client";
import { Card } from "@/components/ui/card";

const REFRESH_MS = 30_000;

function fmtGB(kb: number): string {
  return (kb / 1024 / 1024).toFixed(2) + " GB";
}

function fmtPct(used_kb: number, total_kb: number): string {
  if (!total_kb) return "—";
  return ((100 * used_kb) / total_kb).toFixed(1) + "%";
}

function formatRelative(iso: string | null | undefined): string {
  if (!iso) return "—";
  const ms = Date.now() - new Date(iso).getTime();
  const mins = Math.round(ms / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

function verdict(headroom_pct: number, peak_pct: number): { label: string; tone: string; explain: string } {
  if (peak_pct < 50)
    return {
      label: "Likely overprovisioned",
      tone: "text-amber-900 bg-amber-50 border-amber-300",
      explain:
        "Peak usage stayed below half of installed RAM. Halving the VM would still leave roughly 2× headroom over peak — safe target for a downsize.",
    };
  if (peak_pct < 70)
    return {
      label: "Comfortably sized",
      tone: "text-emerald-900 bg-emerald-50 border-emerald-300",
      explain:
        "Peak usage is in the 50–70% band — enough headroom for the kernel page cache and an unexpected spike. Downsize would be tight; keep as-is.",
    };
  if (peak_pct < 90)
    return {
      label: "Right-sized",
      tone: "text-blue-900 bg-blue-50 border-blue-300",
      explain:
        "Peak usage 70–90%. No urgent action needed; revisit if peaks climb above 90% sustained.",
    };
  return {
    label: "Tight — consider scaling up",
    tone: "text-red-900 bg-red-50 border-red-300",
    explain:
      "Peak usage above 90% of installed RAM. Risk of OOM on the next spike — provision more RAM or trim what's running.",
  };
}

function Sparkline({ samples }: { samples: MemorySnapshot[] }) {
  // Series = used = total - available, normalized by max(total) in series.
  if (samples.length < 2) return <p className="text-xs text-neutral-500">Not enough samples to graph yet (need 2+).</p>;
  const total = Math.max(...samples.map((s) => s.mem_total_kb));
  const used = samples.map((s) => s.mem_total_kb - s.mem_available_kb);
  const W = 720;
  const H = 120;
  const step = W / Math.max(1, samples.length - 1);
  const points = used
    .map((u, i) => `${(i * step).toFixed(2)},${(H - (H * u) / total).toFixed(2)}`)
    .join(" ");
  // Reference lines at 50%, 70%, 90%.
  return (
    <svg width="100%" viewBox={`0 0 ${W} ${H}`} className="border rounded bg-neutral-50">
      {[0.5, 0.7, 0.9].map((frac) => (
        <line
          key={frac}
          x1={0}
          x2={W}
          y1={H * (1 - frac)}
          y2={H * (1 - frac)}
          stroke={frac === 0.9 ? "#dc2626" : frac === 0.7 ? "#2563eb" : "#a3a3a3"}
          strokeDasharray="4 4"
          strokeWidth={1}
        />
      ))}
      <polyline points={points} fill="none" stroke="#059669" strokeWidth={1.8} />
      <text x={6} y={H * 0.5 - 4} fontSize={9} fill="#525252">50%</text>
      <text x={6} y={H * 0.3 - 4} fontSize={9} fill="#2563eb">70%</text>
      <text x={6} y={H * 0.1 - 4} fontSize={9} fill="#dc2626">90%</text>
    </svg>
  );
}

export default function MemoryAdminPage() {
  const [data, setData] = useState<MemoryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    try {
      setData(await api.getMemory());
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  useEffect(() => {
    load();
    const t = setInterval(load, REFRESH_MS);
    return () => clearInterval(t);
  }, []);

  if (error) {
    return (
      <Card>
        <p className="text-red-700">Error loading memory data: {error}</p>
      </Card>
    );
  }
  if (!data) return <p>Loading…</p>;

  const cur = data.current;
  const sum = data.summary;
  const peak_pct =
    sum && sum.mem_total_kb ? (100 * sum.peak_used_kb) / sum.mem_total_kb : 0;
  const v = sum ? verdict(sum.headroom_pct, peak_pct) : null;

  return (
    <div className="space-y-6">
      <div>
        <h1>Memory usage</h1>
        <p className="text-sm text-neutral-600 mt-1">
          Host RAM snapshots every 5 minutes via a cron writing
          /var/log/prospectforge_memlog.csv (sysstat-collected sar history
          available separately on the host). This page polls every 30s.
        </p>
      </div>

      {v && (
        <div className={`rounded-lg border px-4 py-3 ${v.tone}`}>
          <div className="font-semibold text-base">Verdict: {v.label}</div>
          <p className="text-sm mt-1">{v.explain}</p>
        </div>
      )}

      {cur && (
        <Card>
          <h3>Right now</h3>
          <dl className="mt-3 grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
            <div>
              <dt className="text-xs uppercase text-neutral-500">Total</dt>
              <dd className="font-medium text-base">{fmtGB(cur.mem_total_kb)}</dd>
            </div>
            <div>
              <dt className="text-xs uppercase text-neutral-500">Used</dt>
              <dd className="font-medium text-base">
                {fmtGB(cur.mem_total_kb - cur.mem_available_kb)}{" "}
                <span className="text-xs text-neutral-500">
                  ({fmtPct(cur.mem_total_kb - cur.mem_available_kb, cur.mem_total_kb)})
                </span>
              </dd>
            </div>
            <div>
              <dt className="text-xs uppercase text-neutral-500">Available</dt>
              <dd className="font-medium text-base">{fmtGB(cur.mem_available_kb)}</dd>
            </div>
            <div>
              <dt className="text-xs uppercase text-neutral-500">Cache + buffers</dt>
              <dd className="font-medium text-base">{fmtGB(cur.cached_kb + cur.buffers_kb)}</dd>
            </div>
          </dl>
          {cur.swap_total_kb > 0 && (
            <p className="text-xs text-neutral-500 mt-3">
              Swap: {fmtGB(cur.swap_total_kb - cur.swap_free_kb)} used of{" "}
              {fmtGB(cur.swap_total_kb)}.
            </p>
          )}
        </Card>
      )}

      {sum && (
        <Card>
          <h3>Trend ({sum.sample_count} samples, {formatRelative(sum.window_start)} → now)</h3>
          <dl className="mt-3 grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
            <div>
              <dt className="text-xs uppercase text-neutral-500">Peak used</dt>
              <dd className="font-medium text-base">
                {fmtGB(sum.peak_used_kb)}{" "}
                <span className="text-xs text-neutral-500">
                  ({fmtPct(sum.peak_used_kb, sum.mem_total_kb)})
                </span>
              </dd>
            </div>
            <div>
              <dt className="text-xs uppercase text-neutral-500">Avg used</dt>
              <dd className="font-medium text-base">
                {fmtGB(sum.avg_used_kb)}{" "}
                <span className="text-xs text-neutral-500">
                  ({fmtPct(sum.avg_used_kb, sum.mem_total_kb)})
                </span>
              </dd>
            </div>
            <div>
              <dt className="text-xs uppercase text-neutral-500">Headroom over peak</dt>
              <dd className="font-medium text-base">{sum.headroom_pct.toFixed(1)}%</dd>
            </div>
            <div>
              <dt className="text-xs uppercase text-neutral-500">Installed</dt>
              <dd className="font-medium text-base">{fmtGB(sum.mem_total_kb)}</dd>
            </div>
          </dl>
          <div className="mt-4">
            <Sparkline samples={data.history} />
            <p className="text-xs text-neutral-500 mt-2">
              Green line = used (total − available). Dashed lines at 50% / 70% / 90% of installed RAM.
            </p>
          </div>
        </Card>
      )}

      <Card>
        <h3>Recent samples</h3>
        <p className="text-xs text-neutral-500 mb-2">
          Latest {Math.min(data.history.length, 30)} of {data.history.length} loaded rows.
        </p>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-neutral-50 text-xs uppercase text-neutral-500">
              <tr>
                <th className="text-left px-3 py-2">Time (UTC)</th>
                <th className="text-right px-3 py-2">Used</th>
                <th className="text-right px-3 py-2">Available</th>
                <th className="text-right px-3 py-2">Cache</th>
                <th className="text-right px-3 py-2">% used</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {data.history.slice(-30).reverse().map((s) => (
                <tr key={s.iso_ts}>
                  <td className="px-3 py-1 font-mono text-xs">{s.iso_ts}</td>
                  <td className="px-3 py-1 text-right tabular-nums">
                    {fmtGB(s.mem_total_kb - s.mem_available_kb)}
                  </td>
                  <td className="px-3 py-1 text-right tabular-nums text-neutral-600">
                    {fmtGB(s.mem_available_kb)}
                  </td>
                  <td className="px-3 py-1 text-right tabular-nums text-neutral-600">
                    {fmtGB(s.cached_kb + s.buffers_kb)}
                  </td>
                  <td className="px-3 py-1 text-right tabular-nums">
                    {fmtPct(s.mem_total_kb - s.mem_available_kb, s.mem_total_kb)}
                  </td>
                </tr>
              ))}
              {data.history.length === 0 && (
                <tr>
                  <td colSpan={5} className="text-center text-neutral-600 py-3">
                    No samples yet — wait 5 minutes for the cron to write the first row.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
