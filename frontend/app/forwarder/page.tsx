"use client";

import { useState } from "react";
import { Users, MessageSquare, Send, Radio, Plus, Loader2, Trash2 } from "lucide-react";
import { useApi, apiSend } from "@/lib/api";
import { mutate } from "swr";
import { PageHeader } from "@/components/PageHeader";
import { StatCard } from "@/components/StatCard";
import { CollapsibleSection } from "@/components/CollapsibleSection";
import { CopyButton } from "@/components/CopyButton";
import { SearchBox } from "@/components/SectionFilters";
import { TableScroll } from "@/components/TableScroll";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fmtNum } from "@/lib/utils";

type Source = {
  key: string;
  kind: "channel" | "group";
  name: string;
  subtitle?: string;
  chat_id?: number | null;
  named?: boolean;
  enabled: boolean;
  today: number;
};

type Dest = {
  key: string;
  chat_id?: number | null;
  purpose: string;
  configured: boolean;
  today: number;
};

function SourceRow({ s, onToggle, onRemove }: {
  s: Source;
  onToggle: (s: Source, v: boolean) => void;
  onRemove?: (s: Source) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-lg border border-border-soft px-3 py-2.5">
      <div className="min-w-0">
        <div className="truncate text-sm text-text">{s.name}</div>
        <div className="flex items-center gap-1 text-[11px] text-text-dim">
          <span className="truncate">{s.subtitle}</span>
          {s.chat_id != null && <CopyButton value={String(s.chat_id)} />}
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-3">
        <span className="text-xs text-text-muted" title="messages seen today">
          {fmtNum(s.today)}
        </span>
        <Switch checked={s.enabled} onCheckedChange={(v) => onToggle(s, v)} />
        {onRemove && (
          <button
            onClick={() => onRemove(s)}
            title="Remove this group"
            className="grid h-6 w-6 place-items-center rounded text-text-dim transition-colors hover:bg-bg-hover hover:text-accent-red"
          >
            <Trash2 size={13} />
          </button>
        )}
      </div>
    </div>
  );
}

// Add by whatever the user has to hand: the group's name, its @username, a
// t.me link, or the raw chat id. Resolution happens server-side through the
// userbot, which is the only thing that can see a private group.
function AddGroup() {
  const [val, setVal] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  async function add() {
    const value = val.trim();
    if (!value) return;
    setBusy(true);
    setMsg(null);
    try {
      const r: any = await apiSend("/api/forwarder/groups", "POST", { value });
      setVal("");
      setMsg({ ok: true, text: `Added ${r.name || r.id} — live now` });
      mutate("/api/forwarder/sources");
      mutate("/api/forwarder/stats");
    } catch (e: any) {
      setMsg({ ok: false, text: String(e?.message || e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mb-3">
      <div className="flex gap-2">
        <Input
          value={val}
          onChange={(e) => setVal(e.target.value)}
          placeholder="Group name  ·  @username  ·  t.me/…  ·  -100123…"
          onKeyDown={(e) => e.key === "Enter" && add()}
        />
        <Button variant="primary" size="sm" disabled={busy || !val.trim()} onClick={add}>
          {busy ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />} Add
        </Button>
      </div>
      {msg && (
        <p className={`mt-1.5 text-[11px] ${msg.ok ? "text-accent-green" : "text-accent-red"}`}>
          {msg.text}
        </p>
      )}
    </div>
  );
}

export default function ForwarderPage() {
  const [q, setQ] = useState("");
  const { data: stats } = useApi<any>("/api/forwarder/stats");
  const { data: sources } = useApi<any>("/api/forwarder/sources");
  const { data: dests } = useApi<any>("/api/forwarder/destinations");

  async function toggle(s: Source, enabled: boolean) {
    await apiSend(`/api/forwarder/sources/${encodeURIComponent(s.key)}`, "PATCH", { enabled });
    mutate("/api/forwarder/sources");
    // A channel row flips a registry service, so Settings must agree.
    if (s.kind === "channel") mutate("/api/settings/services");
  }

  async function remove(s: Source) {
    if (!confirm(`Stop mirroring "${s.name}"?`)) return;
    await apiSend(`/api/forwarder/groups/${s.chat_id}`, "DELETE");
    mutate("/api/forwarder/sources");
    mutate("/api/forwarder/stats");
  }

  const all: Source[] = sources?.items ?? [];
  const channels = all.filter((s) => s.kind === "channel");
  const needle = q.trim().toLowerCase();
  const groups = all
    .filter((s) => s.kind === "group")
    .filter((s) => !needle
      || s.name.toLowerCase().includes(needle)
      || (s.subtitle ?? "").toLowerCase().includes(needle));

  return (
    <div className="space-y-5">
      <PageHeader
        title="Telegram Forwarder"
        subtitle="Monitoring source channels · forwarding to destination groups"
      />
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="Total Sources" value={stats?.total_sources ?? 0} icon={Users} tone="purple" />
        <StatCard label="Premium Groups" value={stats?.total_groups ?? 0} icon={Users} tone="blue" />
        <StatCard label="Messages Today" value={fmtNum(stats?.messages_today)} icon={MessageSquare} tone="green" />
        <StatCard label="Forwarded Today" value={fmtNum(stats?.forwarded_today)} icon={Send} tone="cyan" />
      </div>

      <CollapsibleSection
        id="fwd-channels"
        title="Signal Channels"
        icon={<Radio size={14} />}
        count={channels.length}
      >
        <p className="mb-3 text-xs text-text-dim">
          The named channels the userbot reads. Each switch is the same one as
          Settings → Bots, so the two cannot disagree.
        </p>
        <div className="space-y-1">
          {channels.map((s) => <SourceRow key={s.key} s={s} onToggle={toggle} />)}
          {channels.length === 0 && (
            <span className="text-xs text-text-dim">No source channels configured in .env</span>
          )}
        </div>
      </CollapsibleSection>

      <CollapsibleSection
        id="fwd-groups"
        title="Premium Groups"
        icon={<Users size={14} />}
        count={groups.length}
        controls={<SearchBox value={q} onChange={setQ} placeholder="group name / id" />}
      >
        <p className="mb-3 text-xs text-text-dim">
          Every group the userbot mirrors, with its chat id under the name. The
          name is worked out on its own — the group's live Telegram title as
          soon as it posts, otherwise the seeded one.
        </p>
        <AddGroup />
        <TableScroll>
          <div className="space-y-1">
            {groups.map((s) => (
              <SourceRow key={s.key} s={s} onToggle={toggle} onRemove={remove} />
            ))}
            {groups.length === 0 && (
              <span className="text-xs text-text-dim">
                {needle ? "No group matches this search" : "No premium groups"}
              </span>
            )}
          </div>
        </TableScroll>
      </CollapsibleSection>

      <CollapsibleSection
        id="fwd-dests"
        title="Destination Groups"
        icon={<Send size={14} />}
        count={(dests?.items ?? []).filter((d: Dest) => d.configured).length}
      >
        <p className="mb-3 text-xs text-text-dim">
          Where filtered signals are forwarded, read from <code>.env</code>. Sent
          by the userbot, so the bot does not need to be a member.
        </p>
        <div className="space-y-1">
          {(dests?.items ?? []).map((d: Dest) => (
            <div key={d.key} className="flex items-center justify-between gap-3 rounded-lg border border-border-soft px-3 py-2.5">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-xs text-brand-soft">{d.key}</span>
                  {!d.configured && <Badge variant="amber">not set</Badge>}
                </div>
                <div className="truncate text-[11px] text-text-dim">{d.purpose}</div>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                {d.chat_id != null && (
                  <>
                    <code className="rounded bg-bg-soft px-2 py-0.5 font-mono text-[11px] text-text-muted">
                      {d.chat_id}
                    </code>
                    <CopyButton value={String(d.chat_id)} />
                  </>
                )}
                <span className="w-10 text-right text-xs text-text-muted" title="forwarded today">
                  {fmtNum(d.today)}
                </span>
              </div>
            </div>
          ))}
        </div>
      </CollapsibleSection>
    </div>
  );
}
