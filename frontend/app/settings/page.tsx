"use client";

import { useState } from "react";
import { Activity, Bot, Brain, Link2, Radio, Plus, X, Tag, Search, Hash, Loader2, Twitter, Target } from "lucide-react";
import { useApi, apiGet, apiSend } from "@/lib/api";
import { mutate } from "swr";
import { PageHeader } from "@/components/PageHeader";
import { CopyButton } from "@/components/CopyButton";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { CredentialsManager } from "@/components/features/CredentialsManager";

type Svc = { id: string; label: string; enabled: boolean; category: string;
             // Optional sub-heading inside a section. Set in the registry, so
             // a section that wants blocks gets them and one that does not is
             // unchanged.
             group?: string | null };

const CAT = {
  bot: { title: "Bots", icon: Bot, desc: "Enable or disable each bot / signal source" },
  ai: { title: "AI", icon: Brain, desc: "The narrative agent, its feed, and what it is asked" },
  chain: { title: "Chains", icon: Link2, desc: "Turn a whole chain on or off" },
  rpc: { title: "RPCs", icon: Radio, desc: "Toggle individual RPC endpoints" },
  // Its own section rather than eleven more switches in Bots: they belong to
  // one feature and are only understandable together. Two panels share it, so
  // it is drawn in blocks — see `group` on Svc.
  // Its own section for the same reason: the tracker, the chains it prices and
  // the endpoints it prices them on are only understandable together.
  rsi: { title: "RSI Controller", icon: Activity,
         desc: "Relative strength on the tokens you add. A chain needs both its "
             + "switches — the chain and its endpoints — to be sampled." },
  // Its own section rather than a switch inside Chains or RPCs: those answer
  // "is SOL on" and "is this endpoint used", while this answers "which of the
  // two sources feeding the SOL panel is running".
  sol: { title: "Solana Sources", icon: Radio,
         desc: "The SOL panel is fed by two independent sources. The GMGN feed "
             + "is always on; the on-chain socket below is the one you can stop." },
  // Shares the RSI nav on the dashboard, its own section here: it answers a
  // different question and has its own worker, chains and endpoints.
  mcap: { title: "Market Cap Alert", icon: Target,
          desc: "Watches the tokens you add and says when one reaches the market "
              + "cap you set. A chain needs both its switches — the chain and "
              + "its endpoints — to be read." },
  rbhx: { title: "Robinhood Monitors", icon: Twitter,
          desc: "Who is behind a Robinhood launch, read off the token's own metadata. "
              + "Two panels, one socket — each block is one of them." },
};

function ServiceGroup({ cat, items }: { cat: keyof typeof CAT; items: Svc[] }) {
  const meta = CAT[cat];
  const Icon = meta.icon;
  const [busy, setBusy] = useState<string | null>(null);

  // One block per group, in the order the registry lists them — the registry
  // is where "which panel does this switch belong to" is already decided, so
  // it is not decided a second time here. No groups means one nameless block,
  // which renders as the flat list every other section has always been.
  const blocks: [string, Svc[]][] = [];
  for (const s of items) {
    const title = s.group || "";
    const last = blocks[blocks.length - 1];
    if (last && last[0] === title) last[1].push(s);
    else blocks.push([title, [s]]);
  }

  async function toggle(svc: Svc, enabled: boolean) {
    setBusy(svc.id);
    // optimistic
    mutate("/api/settings/services", (cur: any) => {
      if (!cur) return cur;
      return { ...cur, [cat]: cur[cat].map((s: Svc) => s.id === svc.id ? { ...s, enabled } : s) };
    }, false);
    try {
      await apiSend(`/api/settings/services/${svc.id}`, "PATCH", { enabled });
    } finally {
      mutate("/api/settings/services");
      setBusy(null);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><Icon size={14} /> {meta.title}</CardTitle>
        <span className="text-[11px] text-text-dim">{items.filter((s) => s.enabled).length}/{items.length} on</span>
      </CardHeader>
      <CardContent className="space-y-1">
        <p className="mb-3 text-xs text-text-dim">{meta.desc}</p>
        {blocks.map(([title, rows]) => (
          <div key={title || "_"} className={title ? "pt-2 first:pt-0" : undefined}>
            {/* Only a section whose registry entries carry a group draws
                headings — every other section renders exactly as before. */}
            {title && (
              <div className="mb-1.5 flex items-baseline justify-between px-1">
                <span className="text-[11px] font-semibold uppercase tracking-wide text-text-muted">
                  {title}
                </span>
                <span className="text-[10px] text-text-dim">
                  {rows.filter((s) => s.enabled).length}/{rows.length} on
                </span>
              </div>
            )}
            <div className="space-y-1">
              {rows.map((s) => (
                <div key={s.id} className={cn(
                  "flex items-center justify-between rounded-lg border border-border-soft px-3 py-2.5 transition-colors",
                  s.enabled ? "bg-bg-hover/30" : "bg-transparent"
                )}>
                  <div className="flex items-center gap-2.5">
                    <span className={cn("h-2 w-2 rounded-full", s.enabled ? "bg-accent-green" : "bg-text-dim")} />
                    <span className={cn("text-sm", s.enabled ? "text-text" : "text-text-muted")}>{s.label}</span>
                  </div>
                  <Switch checked={s.enabled} disabled={busy === s.id}
                    onCheckedChange={(v) => toggle(s, v)} />
                </div>
              ))}
            </div>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

/* One editable keyword list. Two of them exist — the forwarder's detection
 * keywords and the Robinhood Launchpad ones — over different collections but
 * the same add/remove/whole-word rule, so `path` is the only difference. */
function KeywordManager({ path = "/api/settings/keywords", title = "Detection Keywords",
                          hint }: { path?: string; title?: string; hint?: React.ReactNode }) {
  const { data } = useApi<any>(path);
  const [val, setVal] = useState("");
  const items: string[] = data?.items ?? [];

  async function add() {
    if (!val.trim()) return;
    await apiSend(path, "POST", { action: "add", value: val.trim() });
    setVal(""); mutate(path);
  }
  async function remove(w: string) {
    await apiSend(path, "POST", { action: "remove", value: w });
    mutate(path);
  }

  return (
    <Card>
      <CardHeader><CardTitle className="flex items-center gap-2"><Tag size={14} /> {title}</CardTitle>
        <span className="text-[11px] text-text-dim">{items.length}</span>
      </CardHeader>
      <CardContent>
        <p className="mb-3 text-xs text-text-dim">
          {hint ?? <>Whole-word match only — <span className="text-text-muted">“ai”</span> matches “new <b>ai</b> agent”, not “m<b>ai</b>n”.</>}
        </p>
        <div className="flex gap-2">
          <Input value={val} onChange={(e) => setVal(e.target.value)}
            placeholder="Add a keyword…" onKeyDown={(e) => e.key === "Enter" && add()} />
          <Button variant="primary" size="sm" onClick={add}><Plus size={14} /> Add</Button>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          {items.map((w) => (
            <span key={w} className="flex items-center gap-1.5 rounded-md border border-border bg-bg-soft px-2 py-1 text-xs">
              {w}
              <button onClick={() => remove(w)} className="text-text-dim hover:text-accent-red"><X size={12} /></button>
            </span>
          ))}
          {items.length === 0 && <span className="text-xs text-text-dim">No keywords yet</span>}
        </div>
      </CardContent>
    </Card>
  );
}

// The list the AI is asked to choose between. Numbered because that is exactly
// how it reaches the model — the prompt is this list, in this order — so what
// is on screen is what is being asked.
type Narrative = { text: string; enabled: boolean };

function NarrativeManager() {
  const { data } = useApi<any>("/api/settings/narratives");
  const [val, setVal] = useState("");
  const [busy, setBusy] = useState(false);
  const items: Narrative[] = data?.items ?? [];
  const on = items.filter((n) => n.enabled);

  async function send(body: any) {
    await apiSend("/api/settings/narratives", "POST", body);
    mutate("/api/settings/narratives");
  }
  async function add() {
    const text = val.trim();
    if (!text || busy) return;
    setBusy(true);
    try { await send({ action: "add", value: text }); setVal(""); }
    finally { setBusy(false); }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Brain size={14} /> AI Narratives
        </CardTitle>
        <span className="text-[11px] text-text-dim">{on.length}/{items.length} on</span>
      </CardHeader>
      <CardContent>
        <p className="mb-3 text-xs text-text-dim">
          What the model is asked to look for in a post. Added here, it is in the
          next launch's prompt — no restart. Switch one off to drop it from the
          prompt without losing it; the × is for the ones you are done with.
        </p>
        <div className="flex gap-2">
          <Input value={val} onChange={(e) => setVal(e.target.value)}
            placeholder="e.g. Related to a new football signing"
            onKeyDown={(e) => e.key === "Enter" && add()} />
          <Button variant="primary" size="sm" onClick={add} disabled={busy}>
            <Plus size={14} /> Add
          </Button>
        </div>
        <div className="mt-3 space-y-1.5">
          {/* Numbered by what the model actually sees: an off narrative is not
              in the prompt, so it does not take a number either. */}
          {items.map((n) => {
            const idx = n.enabled ? on.findIndex((o) => o.text === n.text) + 1 : 0;
            return (
              <div key={n.text} className={cn(
                "flex items-center gap-2 rounded-md border border-border-soft px-2.5 py-1.5 text-xs",
                n.enabled ? "bg-bg-soft" : "bg-transparent",
              )}>
                <span className="w-5 shrink-0 text-right font-mono text-text-dim">
                  {idx ? `${idx}.` : "—"}
                </span>
                <span className={cn("min-w-0 flex-1", n.enabled ? "text-text-muted" : "text-text-dim line-through")}>
                  {n.text}
                </span>
                <Switch checked={n.enabled}
                  onCheckedChange={(v) => send({ action: "toggle", value: n.text, enabled: v })} />
                <button onClick={() => send({ action: "remove", value: n.text })}
                  className="shrink-0 text-text-dim hover:text-accent-red"><X size={12} /></button>
              </div>
            );
          })}
          {items.length === 0 && (
            <span className="text-xs text-text-dim">No narratives — the model has nothing to match</span>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

type Chat = {
  id: number; title: string; type: string;
  username?: string | null; source?: string;
};

function ChatRow({ c }: { c: Chat }) {
  return (
    <div className="flex items-center justify-between gap-2 rounded-lg border border-border-soft px-3 py-2">
      <div className="min-w-0">
        <div className="truncate text-sm text-text">{c.title}</div>
        <div className="flex items-center gap-1.5 text-[11px] text-text-dim">
          <span className="capitalize">{c.type}</span>
          {c.username && <span className="truncate">· @{c.username}</span>}
          {c.source && <span className="truncate">· {c.source}</span>}
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-1">
        <code className="rounded bg-bg-soft px-2 py-1 font-mono text-xs text-brand-soft">
          {c.id}
        </code>
        <CopyButton value={String(c.id)} />
      </div>
    </div>
  );
}

function ChatIdFinder() {
  // The "seen" list is what makes a brand-new private group findable: it has
  // no @username, so the only way a bot learns its id is being added to it.
  const { data: seen } = useApi<any>("/api/chat-id/seen");
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<any>(null);

  async function find() {
    const query = q.trim();
    if (!query) return;
    setBusy(true);
    try {
      setRes(await apiGet(`/api/chat-id/lookup?q=${encodeURIComponent(query)}`));
      mutate("/api/chat-id/seen");
    } catch (e: any) {
      setRes({ matches: [], error: String(e?.message || e) });
    } finally {
      setBusy(false);
    }
  }

  const discovery: boolean = seen?.discovery ?? false;
  const items: Chat[] = seen?.items ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><Hash size={14} /> Find Chat ID</CardTitle>
      </CardHeader>
      <CardContent>
        <p className="mb-3 text-xs text-text-dim">
          Group ka naam, @username, t.me link ya numeric id daalo — sirf chat ID
          nikalne ke liye. Yahan se kahin add nahi hota, ID copy karke{" "}
          <code>.env</code> me paste kar lena.
        </p>

        <div className="flex gap-2">
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="My Alert Group  ·  @channel  ·  t.me/…  ·  -100123…"
            onKeyDown={(e) => e.key === "Enter" && find()}
          />
          <Button variant="primary" size="sm" disabled={busy || !q.trim()} onClick={find}>
            {busy ? <Loader2 size={14} className="animate-spin" /> : <Search size={14} />} Find
          </Button>
        </div>

        {res && (
          <div className="mt-3 space-y-1.5">
            {res.matches?.length > 0
              ? res.matches.map((c: Chat) => <ChatRow key={c.id} c={c} />)
              : <p className="rounded-lg border border-accent-amber/40 px-3 py-2 text-xs text-text-muted">
                  {res.error || "no match"}
                </p>}
          </div>
        )}

        <div className="mt-4 border-t border-border-soft pt-3">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs text-text-muted">Groups the bot is in</span>
            <Badge variant={discovery ? "green" : "amber"}>
              {discovery ? "listening" : "not listening"}
            </Badge>
          </div>
          {!discovery && (
            <p className="mb-2 text-[11px] text-text-dim">{seen?.note}</p>
          )}
          <p className="mb-2 text-[11px] text-text-dim">
            Naya group banao → usme bot ko add karo → yahan turant naam aur ID aa jayegi.
          </p>
          <div className="space-y-1.5">
            {items.map((c) => <ChatRow key={c.id} c={c} />)}
            {items.length === 0 && (
              <span className="text-xs text-text-dim">No groups yet</span>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

export default function SettingsPage() {
  const { data } = useApi<any>("/api/settings/services");

  return (
    <div className="space-y-5">
      <PageHeader title="Settings" subtitle="Toggle bots, chains, RPC endpoints, keywords and groups" />

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        <ServiceGroup cat="bot" items={data?.bot ?? []} />
        {/* Everything the AI agent needs in one column: its switches first,
            then the list it is asked to match against. They were spread across
            Bots and the far column, which meant changing how it behaves took
            two places on the page. */}
        <div className="space-y-5">
          <ServiceGroup cat="ai" items={data?.ai ?? []} />
          <CredentialsManager only="AI" />
          <NarrativeManager />
        </div>
        <div className="space-y-5">
          <ServiceGroup cat="rbhx" items={data?.rbhx ?? []} />
          <ServiceGroup cat="rsi" items={data?.rsi ?? []} />
          <ServiceGroup cat="mcap" items={data?.mcap ?? []} />
          <ServiceGroup cat="chain" items={data?.chain ?? []} />
          {/* Directly under Chains: "SOL is on" and "which SOL source is on"
              are read one after the other, not hunted for separately. */}
          <ServiceGroup cat="sol" items={data?.sol ?? []} />
          <ServiceGroup cat="rpc" items={data?.rpc ?? []} />
          {/* The endpoint URLs themselves live on the RPC Monitor page, next to
              the live connection status they actually affect. */}
          <KeywordManager />
          {/* The Launchpad Monitor's own list, matched against the account's
              bio. Its own collection, so editing one never touches the other. */}
          <KeywordManager
            path="/api/launchpad/keywords"
            title="Robinhood Keywords Match"
            hint={<>Matched against the X account&rsquo;s bio on every Robinhood launch.
                    A hit is highlighted in the Launchpad Monitor&rsquo;s Text column and
                    leads its Telegram alert. Whole-word only — “AI” matches “AI agent”,
                    not “said”.</>} />
          <ChatIdFinder />
          <CredentialsManager exclude={["AI", "RPC Endpoints"]} />
        </div>
      </div>
    </div>
  );
}
