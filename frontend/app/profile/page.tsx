"use client";

import { useState } from "react";
import { BadgeCheck, KeyRound, Loader2, Mail, Send, Unlink } from "lucide-react";
import { apiSend } from "@/lib/api";
import { useAccount, statusLine, statusTone } from "@/lib/account";
import { PageHeader } from "@/components/PageHeader";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

/* Profile — the account's own page.
 *
 * Four things live here because they are the four an account ever needs: what
 * plan is running and for how long, how much of it is being used, where alerts
 * go, and the two credentials. Nothing operational: this page is for the person
 * who bought the product, not for the person running it. */

function Meter({ label, used, limit }: { label: string; used: number; limit: number }) {
  const pct = limit > 0 ? Math.min(100, Math.round((used / limit) * 100)) : 0;
  const tone = pct >= 100 ? "bg-accent-red" : pct >= 80 ? "bg-accent-amber" : "bg-accent-green";
  return (
    <div>
      <div className="flex items-baseline justify-between text-xs">
        <span className="text-text-muted">{label}</span>
        <span className="font-mono text-text-dim">{used} / {limit}</span>
      </div>
      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-bg-hover">
        <div className={`h-full ${tone}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function TelegramCard() {
  const { account, reload } = useAccount();
  const [busy, setBusy] = useState(false);
  const [link, setLink] = useState("");
  const [error, setError] = useState("");

  async function connect() {
    setBusy(true); setError(""); setLink("");
    try {
      const got = await apiSend("/api/account/telegram/link", "POST");
      setLink(got.url);
      // The bind happens in Telegram, so nothing here can know when it lands —
      // a refresh a few seconds later is what picks it up.
      setTimeout(reload, 8000);
    } catch (e: any) {
      setError(e?.message || "Could not create a link");
    } finally { setBusy(false); }
  }

  async function disconnect() {
    setBusy(true);
    try { await apiSend("/api/account/telegram/link", "DELETE"); await reload(); }
    finally { setBusy(false); setLink(""); }
  }

  const linked = account?.telegram_linked;
  const allowed = account?.limits?.telegram_alerts;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><Send size={14} /> Telegram alerts</CardTitle>
        {linked && <Badge variant="green">connected</Badge>}
      </CardHeader>
      <CardContent>
        {!allowed ? (
          <p className="text-xs text-text-dim">
            Your plan shows alerts on the dashboard. Telegram alerts come with a
            paid plan — your RSI and Market Cap alerts then arrive in your own
            chat with the bot, not in any shared group.
          </p>
        ) : linked ? (
          <>
            <p className="text-xs text-text-dim">
              Your RSI and Market Cap alerts arrive in your own chat with the bot.
            </p>
            <Button size="sm" variant="outline" className="mt-3" onClick={disconnect}
                    disabled={busy}>
              <Unlink size={13} className="mr-1" /> Disconnect
            </Button>
          </>
        ) : (
          <>
            <p className="text-xs text-text-dim">
              Connect once and every alert you set up arrives on your phone. Only
              your own — nobody else&rsquo;s alerts land in your chat, and yours
              land in nobody else&rsquo;s.
            </p>
            <Button size="sm" variant="primary" className="mt-3" onClick={connect}
                    disabled={busy}>
              {busy ? <Loader2 size={13} className="animate-spin" /> : "Connect Telegram"}
            </Button>
            {link && (
              <div className="mt-3 rounded-lg border border-border-soft bg-bg-soft/40 p-3">
                <p className="text-xs text-text-muted">
                  Open this link and press <b>Start</b>. It works once and expires
                  in fifteen minutes.
                </p>
                <a href={link} target="_blank" rel="noopener noreferrer"
                   className="mt-2 block break-all font-mono text-[11px] text-brand-soft hover:underline">
                  {link}
                </a>
              </div>
            )}
            {error && <p className="mt-2 text-xs text-accent-red">{error}</p>}
          </>
        )}
      </CardContent>
    </Card>
  );
}

function CredentialsCard() {
  const { account, reload } = useAccount();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [email, setEmail] = useState("");
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function save(payload: any, ok: string) {
    setBusy(true); setError(""); setMsg("");
    try {
      await apiSend("/api/account/me", "PATCH", payload);
      setMsg(ok); setCurrent(""); setNext(""); setEmail("");
      await reload();
    } catch (e: any) { setError(e?.message || "That did not work"); }
    finally { setBusy(false); }
  }

  return (
    <Card>
      <CardHeader><CardTitle className="flex items-center gap-2">
        <KeyRound size={14} /> Sign-in details
      </CardTitle></CardHeader>
      <CardContent className="space-y-4">
        <div>
          <p className="mb-2 text-xs text-text-dim">Change password</p>
          <Input type="password" placeholder="current password" value={current}
                 onChange={(e) => setCurrent(e.target.value)} className="h-8 text-xs" />
          <Input type="password" placeholder="new password" value={next}
                 onChange={(e) => setNext(e.target.value)} className="mt-2 h-8 text-xs" />
          <Button size="sm" variant="outline" className="mt-2"
                  disabled={busy || !current || !next}
                  onClick={() => save({ current_password: current, password: next },
                                      "Password changed")}>
            Change password
          </Button>
        </div>
        <div className="border-t border-border-soft pt-4">
          <p className="mb-2 text-xs text-text-dim">
            Change email — the new address has to be confirmed before it is used
            for anything.
          </p>
          <div className="flex items-center gap-2">
            <Mail size={13} className="text-text-dim" />
            <span className="font-mono text-[11px] text-text-muted">{account?.email}</span>
            {account?.email_verified
              ? <Badge variant="green">confirmed</Badge>
              : <Badge variant="amber">not confirmed</Badge>}
          </div>
          <Input type="email" placeholder="new email" value={email}
                 onChange={(e) => setEmail(e.target.value)} className="mt-2 h-8 text-xs" />
          <Button size="sm" variant="outline" className="mt-2" disabled={busy || !email}
                  onClick={() => save({ email }, "Confirmation sent to the new address")}>
            Change email
          </Button>
        </div>
        {msg && <p className="text-xs text-accent-green">{msg}</p>}
        {error && <p className="text-xs text-accent-red">{error}</p>}
      </CardContent>
    </Card>
  );
}

export default function ProfilePage() {
  const { account, loading } = useAccount();

  if (loading) {
    return <div className="grid h-64 place-items-center">
      <Loader2 size={18} className="animate-spin text-text-dim" />
    </div>;
  }

  const limits = account?.limits ?? {};
  return (
    <div className="space-y-5">
      <PageHeader title="Profile" subtitle="Your account, your plan, and where your alerts go" />

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2"><BadgeCheck size={14} /> Plan</CardTitle>
            <Badge variant={statusTone(account)}>{account?.status}</Badge>
          </CardHeader>
          <CardContent>
            <p className="text-lg font-semibold text-text">{account?.plan_label}</p>
            <p className="mt-1 text-xs text-text-dim">{statusLine(account)}</p>
            {account?.expires_at ? (
              <p className="mt-1 text-[11px] text-text-dim">
                Until {new Date(account.expires_at * 1000).toLocaleDateString()}
              </p>
            ) : null}
            <div className="mt-4 space-y-2 text-[11px] text-text-dim">
              <div>Checks every <b>{String(limits.min_cadence)}s</b> at the fastest</div>
              <div>Telegram alerts: <b>{limits.telegram_alerts ? "yes" : "dashboard only"}</b></div>
            </div>
            <a href="/plan">
              <Button size="sm" variant="primary" className="mt-4 w-full justify-center">
                {account?.status === "active" ? "Extend or change plan" : "Choose a plan"}
              </Button>
            </a>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>What you are using</CardTitle></CardHeader>
          <CardContent className="space-y-4">
            <Meter label="RSI tokens" used={account?.usage?.rsi_tokens ?? 0}
                   limit={Number(limits.rsi_tokens ?? 0)} />
            <Meter label="Market Cap tokens" used={account?.usage?.mcap_tokens ?? 0}
                   limit={Number(limits.mcap_tokens ?? 0)} />
            <Meter label="Market Cap checks today"
                   used={account?.usage?.mcap_checks_today ?? 0}
                   limit={Number(limits.mcap_checks_per_day ?? 0)} />
            <p className="text-[11px] text-text-dim">
              Limits are per account and reset with the plan; checks reset daily.
            </p>
          </CardContent>
        </Card>

        <TelegramCard />
      </div>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        <CredentialsCard />
      </div>
    </div>
  );
}
