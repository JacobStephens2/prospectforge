"use client";

import { useEffect, useState } from "react";
import { api, type Suppression } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/NativeSelect";

export default function SuppressionsPage() {
  const [items, setItems] = useState<Suppression[]>([]);
  const [type, setType] = useState("email");
  const [value, setValue] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function load() {
    try {
      setItems(await api.listSuppressions());
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
      await api.createSuppression({ type, value, reason });
      setValue("");
      setReason("");
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function remove(id: string) {
    if (!confirm("Remove this suppression?")) return;
    try {
      await api.deleteSuppression(id);
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <div className="space-y-6">
      <h1>Suppressions</h1>

      <Card>
        <h3>Add a suppression</h3>
        <form onSubmit={submit} className="grid grid-cols-1 md:grid-cols-4 gap-3 mt-3">
          <div>
            <Label htmlFor="type">Type</Label>
            <Select id="type" value={type} onChange={(e) => setType(e.target.value)}>
              <option value="email">email</option>
              <option value="domain">domain</option>
            </Select>
          </div>
          <div className="md:col-span-2">
            <Label htmlFor="value">Value</Label>
            <Input
              id="value"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder={type === "email" ? "bouncer@school.org" : "spamschool.test"}
              required
            />
          </div>
          <div>
            <Label htmlFor="reason">Reason</Label>
            <Input
              id="reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="hard bounce"
              required
            />
          </div>
          <div className="md:col-span-4">
            <Button type="submit">Add</Button>
            {error && <p className="text-red-700 text-sm mt-2">{error}</p>}
          </div>
        </form>
      </Card>

      <Card className="overflow-x-auto p-0">
        <table className="w-full text-sm">
          <thead className="bg-neutral-50 text-xs uppercase text-neutral-500">
            <tr>
              <th className="text-left px-3 py-2">Type</th>
              <th className="text-left px-3 py-2">Value</th>
              <th className="text-left px-3 py-2">Reason</th>
              <th className="text-left px-3 py-2">Added</th>
              <th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {items.map((s) => (
              <tr key={s.id}>
                <td className="px-3 py-2">{s.type}</td>
                <td className="px-3 py-2">{s.value}</td>
                <td className="px-3 py-2 text-neutral-600">{s.reason}</td>
                <td className="px-3 py-2 text-xs text-neutral-500">
                  {new Date(s.added_at).toLocaleString()}
                </td>
                <td className="px-3 py-2 text-right">
                  <Button variant="destructive" onClick={() => remove(s.id)}>
                    Remove
                  </Button>
                </td>
              </tr>
            ))}
            {items.length === 0 && (
              <tr>
                <td colSpan={5} className="px-3 py-3 text-center text-neutral-600">
                  No suppressions yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
