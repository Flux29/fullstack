"use client";

import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { LoadingState } from "@/components/states";
import { apiClient } from "@/lib/api-client";
import { useAuthStore } from "@/stores";
import type { User } from "@/types";

export default function AuthCallbackPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // The backend redirects with a single-use opaque code — never with tokens.
    // The proxy exchanges it server-to-server and sets the HttpOnly cookies.
    const code = searchParams.get("code");
    const errParam = searchParams.get("error");

    if (errParam) {
      setError(errParam);
      const t = setTimeout(
        () => router.replace(`/login?error=${encodeURIComponent(errParam)}`),
        1500,
      );
      return () => clearTimeout(t);
    }
    if (!code) {
      router.replace("/login?error=missing_code");
      return;
    }

    let cancelled = false;
    (async () => {
      try {
        const data = await apiClient.post<{ user: User; access_token: string }>(
          "/auth/oauth-callback",
          { code },
        );
        if (cancelled) return;
        useAuthStore.getState().setUser(data.user);
        useAuthStore.getState().setAccessToken(data.access_token);
        router.replace("/dashboard");
      } catch {
        if (!cancelled) {
          setError("Sign-in failed");
          router.replace("/login?error=oauth_failed");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [router, searchParams]);

  return (
    <div className="flex min-h-[50vh] items-center justify-center">
      {error ? (
        <p className="text-foreground/65 text-sm">Sign-in failed. Redirecting…</p>
      ) : (
        <LoadingState variant="dot-pulse" label="Completing sign-in…" />
      )}
    </div>
  );
}
