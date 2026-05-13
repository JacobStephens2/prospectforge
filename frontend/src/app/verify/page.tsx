"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Card } from "@/components/ui/card";

export default function VerifyPage() {
  return (
    <Suspense fallback={<p>Verifying…</p>}>
      <VerifyInner />
    </Suspense>
  );
}

function VerifyInner() {
  const router = useRouter();
  const params = useSearchParams();
  const token = params.get("token");
  const next = params.get("next") || "/";

  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) {
      setError("Missing token in URL.");
      return;
    }
    (async () => {
      try {
        const res = await fetch(`/api/auth/verify?token=${encodeURIComponent(token)}`, {
          method: "POST",
        });
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          setError(
            body.detail
              ? `${res.status}: ${body.detail}`
              : `Couldn't verify (${res.status}). Request a new link.`,
          );
          return;
        }
        router.replace(next);
      } catch (e) {
        setError((e as Error).message);
      }
    })();
  }, [token, next, router]);

  return (
    <div className="min-h-screen flex items-center justify-center px-4 bg-brand-surface">
      <Card className="w-full max-w-md text-center">
        <h1 className="text-brand-blue">Signing you in…</h1>
        {!error ? (
          <p className="text-sm text-neutral-600 mt-3">One moment.</p>
        ) : (
          <div className="mt-4 space-y-3">
            <p className="text-brand-red text-sm">{error}</p>
            <p className="text-sm">
              <a href="/login" className="text-brand-blue">Request a new sign-in link</a>
            </p>
          </div>
        )}
      </Card>
    </div>
  );
}
