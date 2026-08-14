"use client";

import { useState } from "react";
import Link from "next/link";
import { Loader2, Lock } from "lucide-react";
import { login } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export default function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await login(username, password);
      // A full load, not router.replace: the Shell — and its WebSocket — is
      // already mounted around the login page, so a client-side navigation
      // would leave the socket connected as "signed out". This also drops any
      // SWR cache from before signing in.
      window.location.href = "/dashboard";
    } catch {
      setError("Invalid username or password");
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-screen place-items-center bg-bg px-4">
      <form
        onSubmit={submit}
        className="w-full max-w-sm rounded-xl border border-border bg-bg-card/60 p-6 backdrop-blur-sm"
      >
        <div className="mb-5 flex items-center gap-2">
          <span className="grid h-9 w-9 place-items-center rounded-lg bg-brand/15 text-brand-soft">
            <Lock size={17} />
          </span>
          <div>
            <h1 className="text-sm font-semibold text-text">BlockChain-Bot</h1>
            <p className="text-[11px] text-text-dim">Sign in to the dashboard</p>
          </div>
        </div>

        <label className="mb-1 block text-xs text-text-muted">Username</label>
        <Input
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoComplete="username"
          autoFocus
        />

        <label className="mb-1 mt-3 block text-xs text-text-muted">Password</label>
        <Input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
        />

        {error && <p className="mt-3 text-xs text-accent-red">{error}</p>}

        <Button
          type="submit"
          variant="primary"
          className="mt-5 w-full justify-center"
          disabled={busy || !username || !password}
        >
          {busy ? <Loader2 size={14} className="animate-spin" /> : "Sign in"}
        </Button>

        <div className="mt-4 flex items-center justify-between text-[11px]">
          <Link href="/forgot" className="text-text-dim hover:text-brand-soft">
            Forgot password?
          </Link>
          <Link href="/register" className="text-brand-soft hover:underline">
            Create an account
          </Link>
        </div>
      </form>
    </div>
  );
}
