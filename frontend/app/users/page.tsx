"use client";

import { useState } from "react";
import { KeyRound, Lock, Plus, Trash2, UserPlus, Users } from "lucide-react";
import { mutate } from "swr";
import { apiSend, useApi } from "@/lib/api";
import { PageHeader } from "@/components/PageHeader";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { fmtDateTime } from "@/lib/utils";
import { cn } from "@/lib/utils";

const KEY = "/api/users";

type User = {
  username: string;
  role: string;
  enabled: boolean;
  created_at?: number;
  created_by?: string;
  last_login?: number | null;
};

export default function UsersPage() {
  const { data } = useApi<any>(KEY, { refreshInterval: 0 });
  const items: User[] = data?.items ?? [];
  const minPassword: number = data?.min_password ?? 8;

  return (
    <div className="space-y-5">
      <PageHeader
        title="User Management"
        subtitle="Accounts that can sign in to this dashboard"
      />

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        <CreateUser minPassword={minPassword} />
        <div className="lg:col-span-2">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Users size={14} /> Accounts
              </CardTitle>
              <span className="text-[11px] text-text-dim">
                {items.filter((u) => u.enabled).length}/{items.length} active
              </span>
            </CardHeader>
            <CardContent>
              <p className="mb-3 text-xs text-text-dim">
                Every account here signs in read-only: it sees the whole
                dashboard and can change nothing, and Forwarder, Commands,
                Settings and this page are closed to it. The admin login comes
                from the server's own configuration and is not listed.
              </p>
              {items.length === 0 ? (
                <div className="rounded-lg border border-dashed border-border px-4 py-10 text-center text-sm text-text-dim">
                  No accounts yet — create one on the left
                </div>
              ) : (
                <div className="space-y-2">
                  {items.map((u) => (
                    <UserRow key={u.username} user={u} minPassword={minPassword} />
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}

function CreateUser({ minPassword }: { minPassword: number }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");

  async function create() {
    setError(""); setDone(""); setBusy(true);
    try {
      await apiSend(KEY, "POST", { username: username.trim(), password });
      // The password is never shown again — it is hashed the moment it lands
      // and nothing can read it back, so this is the one time to write it down.
      setDone(`${username.trim()} created — give them this password now, it cannot be shown again`);
      setUsername(""); setPassword("");
      mutate(KEY);
    } catch (e: any) {
      setError(e?.message || "could not create the account");
    } finally { setBusy(false); }
  }

  const ready = username.trim().length >= 3 && password.length >= minPassword;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><UserPlus size={14} /> New account</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-text-dim">
          There is no sign-up. An account exists because you make it here, and
          you choose its password.
        </p>
        <div>
          <label className="mb-1 block text-[11px] text-text-dim">Username</label>
          <Input value={username} onChange={(e) => setUsername(e.target.value)}
            placeholder="ravi" autoComplete="off" />
          <p className="mt-1 text-[11px] text-text-dim">
            3–32 characters: letters, numbers, dot, dash, underscore
          </p>
        </div>
        <div>
          <label className="mb-1 block text-[11px] text-text-dim">Password</label>
          <Input value={password} onChange={(e) => setPassword(e.target.value)}
            type="password" autoComplete="new-password"
            onKeyDown={(e) => e.key === "Enter" && ready && create()} />
          <p className="mt-1 text-[11px] text-text-dim">
            At least {minPassword} characters
          </p>
        </div>
        <Button variant="primary" size="sm" disabled={!ready || busy} onClick={create}>
          <Plus size={14} /> {busy ? "Creating…" : "Create account"}
        </Button>
        {error && <p className="text-xs text-accent-red">{error}</p>}
        {done && <p className="text-xs text-accent-green">{done}</p>}
      </CardContent>
    </Card>
  );
}

function UserRow({ user, minPassword }: { user: User; minPassword: number }) {
  const [resetting, setResetting] = useState(false);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");

  async function send(method: "PATCH" | "DELETE", body?: any) {
    setBusy(true); setError("");
    try {
      await apiSend(`${KEY}/${encodeURIComponent(user.username)}`, method, body);
      mutate(KEY);
      return true;
    } catch (e: any) {
      setError(e?.message || "that did not work");
      return false;
    } finally { setBusy(false); }
  }

  async function reset() {
    if (await send("PATCH", { password })) {
      setNote("Password changed — give them the new one now");
      setPassword(""); setResetting(false);
    }
  }

  return (
    <div className={cn(
      "rounded-lg border border-border-soft px-3 py-2.5",
      user.enabled ? "bg-bg-soft/60" : "bg-transparent",
    )}>
      <div className="flex flex-wrap items-center gap-2">
        <span className={cn("text-sm font-medium",
          user.enabled ? "text-text" : "text-text-dim line-through")}>
          {user.username}
        </span>
        <Badge variant={user.enabled ? "green" : "gray"}>
          {user.enabled ? "active" : "disabled"}
        </Badge>
        <Badge variant="blue">read-only</Badge>
        <div className="ml-auto flex items-center gap-1.5">
          <Switch checked={user.enabled} disabled={busy}
            onCheckedChange={(v) => send("PATCH", { enabled: v })} />
          <Button size="sm" variant="outline" disabled={busy}
            onClick={() => { setResetting((v) => !v); setNote(""); }}>
            <KeyRound size={13} /> Password
          </Button>
          <Button size="sm" variant="ghost" disabled={busy}
            onClick={() => send("DELETE")}
            className="text-text-dim hover:text-accent-red">
            <Trash2 size={13} />
          </Button>
        </div>
      </div>

      <div className="mt-1 flex flex-wrap gap-x-3 text-[11px] text-text-dim">
        {user.created_at && <span>created {fmtDateTime(user.created_at)}</span>}
        {user.created_by && <span>by {user.created_by}</span>}
        <span>{user.last_login ? `last login ${fmtDateTime(user.last_login)}` : "never signed in"}</span>
      </div>

      {resetting && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <Input value={password} onChange={(e) => setPassword(e.target.value)}
            type="password" autoComplete="new-password"
            placeholder={`New password (${minPassword}+ characters)`}
            className="max-w-[280px]"
            onKeyDown={(e) => e.key === "Enter" && password.length >= minPassword && reset()} />
          <Button size="sm" variant="primary"
            disabled={busy || password.length < minPassword} onClick={reset}>
            Set
          </Button>
          <span className="flex items-center gap-1 text-[11px] text-text-dim">
            <Lock size={11} /> stored hashed — it cannot be read back
          </span>
        </div>
      )}
      {note && <p className="mt-1.5 text-[11px] text-accent-green">{note}</p>}
      {error && <p className="mt-1.5 text-[11px] text-accent-red">{error}</p>}
    </div>
  );
}
