"use client";

import { useState } from "react";
import { Button, Card, Input, Label } from "@/components/ui";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const res = await fetch("/api/auth/request", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });
      if (!res.ok && res.status !== 204) {
        setError(`Couldn't send link (${res.status}). Try again in a minute.`);
      } else {
        setSubmitted(true);
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center px-4 bg-brand-surface">
      <Card className="w-full max-w-md">
        <h1 className="text-brand-blue">Sign in to ProspectForge</h1>
        {!submitted ? (
          <form onSubmit={submit} className="space-y-4 mt-4">
            <p className="text-sm text-neutral-700">
              Enter your Acme email and we&apos;ll send you a one-time sign-in
              link. The link is valid for 15 minutes.
            </p>
            <div>
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                placeholder="you@acme-travel.example"
                autoComplete="email"
                autoFocus
              />
            </div>
            <Button type="submit" disabled={busy || !email}>
              {busy ? "Sending…" : "Send sign-in link"}
            </Button>
            {error && <p className="text-brand-red text-sm">{error}</p>}
          </form>
        ) : (
          <div className="mt-4 space-y-3">
            <p className="text-sm">
              If <strong>{email}</strong> is a permitted address, a sign-in
              link is on its way. Check your inbox.
            </p>
            <p className="text-xs text-neutral-600">
              The link expires in 15 minutes and can be used once. If nothing
              arrives within a couple of minutes, check spam or try again.
            </p>
            <Button variant="secondary" onClick={() => setSubmitted(false)}>
              Use a different email
            </Button>
          </div>
        )}
      </Card>
    </div>
  );
}
