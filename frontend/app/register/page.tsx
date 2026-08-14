"use client";

import { useState } from "react";
import Link from "next/link";
import { CheckCircle2, Loader2, UserPlus } from "lucide-react";
import { apiPublic } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

/* Sign-up.
 *
 * The trial does not start here — it starts when the email is confirmed, and
 * the page says so, because "7 days" that quietly began before you read the
 * first screen is a complaint waiting to happen. */

export default function RegisterPage() {
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState<string>("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    try {
      const got = await apiPublic("/api/account/register",
        { username: username.trim(), email: email.trim(), password });
      setDone(got.message || "Check your email to confirm the address.");
    } catch (err: any) {
      setError(err?.message || "Could not create that account");
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <div className="grid min-h-screen place-items-center bg-bg px-4">
        <div className="w-full max-w-sm rounded-xl border border-border bg-bg-card/60 p-6 text-center backdrop-blur-sm">
          <span className="mx-auto mb-3 grid h-10 w-10 place-items-center rounded-lg bg-accent-green/15 text-accent-green">
            <CheckCircle2 size={20} />
          </span>
          <h1 className="text-sm font-semibold text-text">Almost there</h1>
          <p className="mt-2 text-xs text-text-dim">{done}</p>
          <p className="mt-3 text-[11px] text-text-dim">
            Sent to <span className="text-text-muted">{email}</span>. Nothing
            starts counting until you open that link.
          </p>
          <Link href="/login">
            <Button variant="outline" className="mt-5 w-full justify-center">
              Back to sign in
            </Button>
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="grid min-h-screen place-items-center bg-bg px-4">
      <form onSubmit={submit}
            className="w-full max-w-sm rounded-xl border border-border bg-bg-card/60 p-6 backdrop-blur-sm">
        <div className="mb-5 flex items-center gap-2">
          <span className="grid h-9 w-9 place-items-center rounded-lg bg-brand/15 text-brand-soft">
            <UserPlus size={17} />
          </span>
          <div>
            <h1 className="text-sm font-semibold text-text">Create an account</h1>
            <p className="text-[11px] text-text-dim">
              7 days free — starts when you confirm your email
            </p>
          </div>
        </div>

        <label className="mb-1 block text-xs text-text-muted">Username</label>
        <Input value={username} onChange={(e) => setUsername(e.target.value)}
               autoComplete="username" autoFocus />

        <label className="mb-1 mt-3 block text-xs text-text-muted">Email</label>
        <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)}
               autoComplete="email" />

        <label className="mb-1 mt-3 block text-xs text-text-muted">Password</label>
        <Input type="password" value={password}
               onChange={(e) => setPassword(e.target.value)}
               autoComplete="new-password" />
        <p className="mt-1 text-[11px] text-text-dim">At least 8 characters.</p>

        {error && <p className="mt-3 text-xs text-accent-red">{error}</p>}

        <Button type="submit" variant="primary"
                className="mt-5 w-full justify-center"
                disabled={busy || !username || !email || !password}>
          {busy ? <Loader2 size={14} className="animate-spin" /> : "Create account"}
        </Button>

        <p className="mt-4 text-center text-[11px] text-text-dim">
          Already have one? <Link href="/login" className="text-brand-soft hover:underline">Sign in</Link>
        </p>
      </form>
    </div>
  );
}
