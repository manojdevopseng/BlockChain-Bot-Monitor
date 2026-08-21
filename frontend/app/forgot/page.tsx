"use client";

import { useState } from "react";
import Link from "next/link";
import { KeyRound, Loader2, MailCheck } from "lucide-react";
import { apiPublic } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

/* Forgotten password.
 *
 * The answer is the same whether or not that address has an account — which
 * addresses are registered is not a fact this page hands to whoever is
 * guessing. The wording says "if", and means it. */

export default function ForgotPage() {
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await apiPublic("/api/account/forgot", { email: email.trim() });
    } catch {
      // Deliberately ignored: a failure here would tell the caller something
      // about the address, which is the one thing this endpoint must not do.
    }
    setSent(true);
    setBusy(false);
  }

  return (
    <div className="grid min-h-screen place-items-center bg-bg px-4">
      {sent ? (
        <div className="w-full max-w-sm rounded-xl border border-border bg-bg-card/60 p-6 text-center backdrop-blur-sm">
          <span className="mx-auto mb-3 grid h-10 w-10 place-items-center rounded-lg bg-accent-green/15 text-accent-green">
            <MailCheck size={20} />
          </span>
          <h1 className="text-sm font-semibold text-text">Check your email</h1>
          <p className="mt-2 text-xs text-text-dim">
            If <span className="text-text-muted">{email}</span> has an account, a
            reset link is on its way. It lasts one hour.
          </p>
          <Link href="/login">
            <Button variant="outline" className="mt-5 w-full justify-center">
              Back to sign in
            </Button>
          </Link>
        </div>
      ) : (
        <form onSubmit={submit}
              className="w-full max-w-sm rounded-xl border border-border bg-bg-card/60 p-6 backdrop-blur-sm">
          <div className="mb-5 flex items-center gap-2">
            <span className="grid h-9 w-9 place-items-center rounded-lg bg-brand/15 text-brand-soft">
              <KeyRound size={17} />
            </span>
            <div>
              <h1 className="text-sm font-semibold text-text">Reset your password</h1>
              <p className="text-[11px] text-text-dim">We will email you a link</p>
            </div>
          </div>
          <label className="mb-1 block text-xs text-text-muted">Email</label>
          <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)}
                 autoComplete="email" autoFocus />
          <Button type="submit" variant="primary"
                  className="mt-5 w-full justify-center" disabled={busy || !email}>
            {busy ? <Loader2 size={14} className="animate-spin" /> : "Send reset link"}
          </Button>
          <p className="mt-4 text-center text-[11px] text-text-dim">
            <Link href="/login" className="text-brand-soft hover:underline">Back to sign in</Link>
          </p>
        </form>
      )}
    </div>
  );
}
