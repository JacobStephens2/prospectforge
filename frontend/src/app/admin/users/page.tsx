"use client";

import { useEffect, useState } from "react";
import { api, ApiError, type Me, type UserRow } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/NativeSelect";

const ROLES = ["admin", "reviewer", "sales", "bdr"];

export default function UsersPage() {
  const [me, setMe] = useState<Me | null>(null);
  const [users, setUsers] = useState<UserRow[]>([]);
  const [forbidden, setForbidden] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  const [role, setRole] = useState("reviewer");

  async function load() {
    setError(null);
    try {
      const [meRes, list] = await Promise.all([api.me(), api.listUsers()]);
      setMe(meRes);
      setUsers(list);
    } catch (e) {
      if (e instanceof ApiError && e.status === 403) {
        setForbidden(true);
      } else {
        setError((e as Error).message);
      }
    }
  }
  useEffect(() => {
    load();
  }, []);

  async function addUser(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api.createUser({ email, full_name: fullName, role });
      setEmail("");
      setFullName("");
      setRole("reviewer");
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function changeRole(u: UserRow, newRole: string) {
    setError(null);
    try {
      await api.updateUser(u.id, { role: newRole });
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function toggleActive(u: UserRow) {
    const verb = u.active ? "Revoke access for" : "Restore access for";
    if (!confirm(`${verb} ${u.email}?`)) return;
    setError(null);
    try {
      await api.updateUser(u.id, { active: !u.active });
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  if (forbidden) {
    return (
      <div className="space-y-4">
        <h1>Users</h1>
        <Card>
          <p className="text-sm text-neutral-700">
            This page is restricted to administrators. Ask an admin if you need
            access changed.
          </p>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <h1>Users &amp; access</h1>

      <p className="text-sm text-neutral-600 max-w-3xl">
        This list is the access whitelist for ProspectForge. A login link is
        only ever emailed to an address that appears here and is{" "}
        <span className="font-medium">active</span>. Revoking access
        immediately signs the person out of any open session. Only admins can
        view or change this list.
      </p>

      <Card>
        <h3>Add a person</h3>
        <form onSubmit={addUser} className="grid grid-cols-1 md:grid-cols-4 gap-3 mt-3">
          <div className="md:col-span-2">
            <Label htmlFor="email">Email</Label>
            <Input
              id="email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="person@acme-travel.example"
              required
            />
          </div>
          <div>
            <Label htmlFor="full_name">Full name</Label>
            <Input
              id="full_name"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              placeholder="Jane Doe"
              required
            />
          </div>
          <div>
            <Label htmlFor="role">Role</Label>
            <Select id="role" value={role} onChange={(e) => setRole(e.target.value)}>
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </Select>
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
              <th className="text-left px-3 py-2">Name</th>
              <th className="text-left px-3 py-2">Email</th>
              <th className="text-left px-3 py-2">Role</th>
              <th className="text-left px-3 py-2">Status</th>
              <th className="text-left px-3 py-2">Added</th>
              <th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {users.map((u) => {
              const isSelf = me?.id === u.id;
              return (
                <tr key={u.id} className={u.active ? "" : "bg-neutral-50 text-neutral-400"}>
                  <td className="px-3 py-2">
                    {u.full_name}
                    {isSelf && <span className="ml-1 text-xs text-neutral-400">(you)</span>}
                  </td>
                  <td className="px-3 py-2">{u.email}</td>
                  <td className="px-3 py-2">
                    <Select
                      value={u.role}
                      onChange={(e) => changeRole(u, e.target.value)}
                      disabled={isSelf}
                    >
                      {ROLES.map((r) => (
                        <option key={r} value={r}>
                          {r}
                        </option>
                      ))}
                    </Select>
                  </td>
                  <td className="px-3 py-2">
                    {u.active ? (
                      <span className="text-emerald-700 font-medium">active</span>
                    ) : (
                      <span className="text-neutral-500">revoked</span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-xs text-neutral-500">
                    {new Date(u.created_at).toLocaleDateString()}
                  </td>
                  <td className="px-3 py-2 text-right">
                    <Button
                      variant={u.active ? "destructive" : "outline"}
                      size="sm"
                      onClick={() => toggleActive(u)}
                      disabled={isSelf && u.active}
                      title={isSelf && u.active ? "You can't revoke your own access" : undefined}
                    >
                      {u.active ? "Revoke" : "Restore"}
                    </Button>
                  </td>
                </tr>
              );
            })}
            {users.length === 0 && (
              <tr>
                <td colSpan={6} className="px-3 py-3 text-center text-neutral-600">
                  No users yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
