"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { CheckCircle2, KeyRound, Loader2 } from "lucide-react";
import { apiPublic } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

function Reset() {
  const token = useSearchParams().get("token") || "";
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);
  const mismatch = again.length > 0 && password !== again;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    try {
      await apiPublic("/api/account/reset", { token, password });
      setDone(true);
    } catch (err: any) {
      setError(err?.message || "That link did not work");
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <div className="w-full max-w-sm rounded-xl border border-border bg-bg-card/60 p-6 text-center backdrop-blur-sm">
        <span className="mx-auto mb-3 grid h-10 w-10 place-items-center rounded-lg bg-accent-green/15 text-accent-green">
          <CheckCircle2 size={20} />
        </span>
        <h1 className="text-sm font-semibold text-text">Password changed</h1>
        <p className="mt-2 text-xs text-text-dim">Sign in with the new one.</p>
        <Link href="/login">
          <Button variant="primary" className="mt-5 w-full justify-center">Sign in</Button>
        </Link>
      </div>
    );
  }

  return (
    <form onSubmit={submit}
          className="w-full max-w-sm rounded-xl border border-border bg-bg-card/60 p-6 backdrop-blur-sm">
      <div className="mb-5 flex items-center gap-2">
        <span className="grid h-9 w-9 place-items-center rounded-lg bg-brand/15 text-brand-soft">
          <KeyRound size={17} />
        </span>
        <div>
          <h1 className="text-sm font-semibold text-text">Set a new password</h1>
          <p className="text-[11px] text-text-dim">At least 8 characters</p>
        </div>
      </div>
      <label className="mb-1 block text-xs text-text-muted">New password</label>
      <Input type="password" value={password}
             onChange={(e) => setPassword(e.target.value)}
             autoComplete="new-password" autoFocus />
      <label className="mb-1 mt-3 block text-xs text-text-muted">Again</label>
      <Input type="password" value={again} onChange={(e) => setAgain(e.target.value)}
             autoComplete="new-password" />
      {mismatch && <p className="mt-2 text-xs text-accent-amber">These do not match.</p>}
      {error && <p className="mt-3 text-xs text-accent-red">{error}</p>}
      <Button type="submit" variant="primary" className="mt-5 w-full justify-center"
              disabled={busy || !token || !password || mismatch}>
        {busy ? <Loader2 size={14} className="animate-spin" /> : "Change password"}
      </Button>
      {!token && (
        <p className="mt-3 text-center text-[11px] text-accent-amber">
          This page needs the link from your email.
        </p>
      )}
    </form>
  );
}

export default function ResetPage() {
  return (
    <div className="grid min-h-screen place-items-center bg-bg px-4">
      <Suspense fallback={<Loader2 size={20} className="animate-spin text-text-dim" />}>
        <Reset />
      </Suspense>
    </div>
  );
}
