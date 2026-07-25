"use client";

import { useState } from "react";
import { Bot, Link2, Radio, Plus, X, Tag, KeyRound, Check, Loader2 } from "lucide-react";
import { useApi, apiSend } from "@/lib/api";
import { mutate } from "swr";
import { PageHeader } from "@/components/PageHeader";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

type Svc = { id: string; label: string; enabled: boolean; category: string };

const CAT = {
  bot: { title: "Bots", icon: Bot, desc: "Enable or disable each bot / signal source" },
  chain: { title: "Chains", icon: Link2, desc: "Turn a whole chain on or off" },
  rpc: { title: "RPCs", icon: Radio, desc: "Toggle individual RPC endpoints" },
};

function ServiceGroup({ cat, items }: { cat: keyof typeof CAT; items: Svc[] }) {
  const meta = CAT[cat];
  const Icon = meta.icon;
  const [busy, setBusy] = useState<string | null>(null);

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
        {items.map((s) => (
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
      </CardContent>
    </Card>
  );
}

function KeywordManager() {
  const { data } = useApi<any>("/api/settings/keywords");
  const [val, setVal] = useState("");
  const items: string[] = data?.items ?? [];

  async function add() {
    if (!val.trim()) return;
    await apiSend("/api/settings/keywords", "POST", { action: "add", value: val.trim() });
    setVal(""); mutate("/api/settings/keywords");
  }
  async function remove(w: string) {
    await apiSend("/api/settings/keywords", "POST", { action: "remove", value: w });
    mutate("/api/settings/keywords");
  }

  return (
    <Card>
      <CardHeader><CardTitle className="flex items-center gap-2"><Tag size={14} /> Detection Keywords</CardTitle></CardHeader>
      <CardContent>
        <p className="mb-3 text-xs text-text-dim">
          Whole-word match only — <span className="text-text-muted">“ai”</span> matches “new <b>ai</b> agent”, not “m<b>ai</b>n”.
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

function GroupManager() {
  const { data } = useApi<any>("/api/settings/groups");
  const [val, setVal] = useState("");
  const items = data?.items ?? [];

  async function add() {
    if (!val.trim()) return;
    await apiSend("/api/settings/groups", "POST", { action: "add", value: val.trim() });
    setVal(""); mutate("/api/settings/groups");
  }

  return (
    <Card>
      <CardHeader><CardTitle className="flex items-center gap-2"><Plus size={14} /> Forwarder Groups</CardTitle></CardHeader>
      <CardContent>
        <p className="mb-3 text-xs text-text-dim">Add by @username, t.me link, or numeric chat ID.</p>
        <div className="flex gap-2">
          <Input value={val} onChange={(e) => setVal(e.target.value)}
            placeholder="@channel  ·  t.me/…  ·  -100123…" onKeyDown={(e) => e.key === "Enter" && add()} />
          <Button variant="primary" size="sm" onClick={add}><Plus size={14} /> Add</Button>
        </div>
        <div className="mt-3 space-y-1.5">
          {items.map((g: any, i: number) => (
            <div key={i} className="flex items-center justify-between rounded-lg border border-border-soft px-3 py-2 text-sm">
              <div>
                <div className="text-text">{g.name}</div>
                <div className="text-[11px] text-text-dim">{g.subtitle}</div>
              </div>
              <Badge variant={g.status === "connected" ? "green" : "amber"}>{g.status}</Badge>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function CredentialsManager() {
  const { data } = useApi<any>("/api/settings/credentials");
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const items: Record<string, any> = data?.items ?? {};

  async function save(key: string) {
    const value = (draft[key] ?? "").trim();
    if (!value) return;
    setBusy(key);
    try {
      await apiSend(`/api/settings/credentials/${key}`, "PUT", { value });
      setDraft((d) => ({ ...d, [key]: "" }));
      setSaved(key);
      setTimeout(() => setSaved(null), 2000);
      mutate("/api/settings/credentials");
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card>
      <CardHeader><CardTitle className="flex items-center gap-2"><KeyRound size={14} /> GMGN Credentials</CardTitle></CardHeader>
      <CardContent>
        <p className="mb-3 text-xs text-text-dim">
          GMGN ka fingerprint expire ho jaye to yahin naya paste karo — <code>.env</code> me
          purana replace hoke save hoga aur turant apply bhi ho jayega (restart nahi chahiye).
        </p>
        <div className="space-y-3">
          {Object.entries(items).map(([key, meta]: [string, any]) => (
            <div key={key}>
              <div className="mb-1 flex items-center justify-between">
                <span className="text-xs text-text-muted">{meta.label}</span>
                <Badge variant={meta.set ? "green" : "gray"}>{meta.set ? "set" : "empty"}</Badge>
              </div>
              {meta.set && (
                <div className="mb-1 truncate font-mono text-[11px] text-text-dim">
                  current: {meta.value || "—"}
                </div>
              )}
              <div className="flex gap-2">
                <Input
                  value={draft[key] ?? ""}
                  onChange={(e) => setDraft((d) => ({ ...d, [key]: e.target.value }))}
                  placeholder={`New ${meta.label}…`}
                  onKeyDown={(e) => e.key === "Enter" && save(key)}
                  className="font-mono text-xs"
                />
                <Button variant="primary" size="sm" disabled={busy === key || !(draft[key] ?? "").trim()}
                  onClick={() => save(key)}>
                  {busy === key ? <Loader2 size={14} className="animate-spin" />
                    : saved === key ? <Check size={14} /> : "Save"}
                </Button>
              </div>
            </div>
          ))}
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
        <div className="space-y-5">
          <ServiceGroup cat="chain" items={data?.chain ?? []} />
          <ServiceGroup cat="rpc" items={data?.rpc ?? []} />
        </div>
        <div className="space-y-5">
          <KeywordManager />
          <CredentialsManager />
          <GroupManager />
        </div>
      </div>
    </div>
  );
}
