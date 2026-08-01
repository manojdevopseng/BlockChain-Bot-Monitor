"use client";

import { useState } from "react";
import { Users, MessageSquare, Send, Radio, Plus, Loader2, Star, Trash2 } from "lucide-react";
import { useApi, apiSend } from "@/lib/api";
import { mutate } from "swr";
import { PageHeader } from "@/components/PageHeader";
import { StatCard } from "@/components/StatCard";
import { CollapsibleSection } from "@/components/CollapsibleSection";
import { CopyButton } from "@/components/CopyButton";
import { FilterTabs, SearchBox } from "@/components/SectionFilters";
import { TableScroll } from "@/components/TableScroll";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ChipStyleEditor } from "@/components/ChipStyleEditor";
import { ChipStyle } from "@/components/GroupChip";
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
  // Set here, read by the Detections table's Groups column. Absent = default chip.
  chip?: ChipStyle | null;
  // Starred for the Important Caller mirror.
  ic?: boolean;
};

type Dest = {
  key: string;
  chat_id?: number | null;
  purpose: string;
  configured: boolean;
  today: number;
};

// Which callers the Premium Groups list is showing. Starring is invisible in a
// list of 116 rows otherwise — you would have to scroll it looking for filled
// stars to answer "who did I add?".
type IcFilter = "all" | "ic" | "not";

const IC_TABS = [
  { id: "all", label: "All" },
  { id: "ic", label: "⭐ In IC" },
  { id: "not", label: "Not in IC" },
] as const satisfies readonly { id: IcFilter; label: string }[];

function SourceRow({ s, onToggle, onRemove, onChip, onIc }: {
  s: Source;
  onToggle: (s: Source, v: boolean) => void;
  onRemove?: (s: Source) => void;
  onChip?: (s: Source, chip: ChipStyle | null) => Promise<void>;
  onIc?: (s: Source, on: boolean) => Promise<void>;
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
        {onIc && (
          <button
            onClick={() => onIc(s, !s.ic)}
            title={s.ic
              ? "In Important Caller — click to stop mirroring this caller there"
              : "Copy to IC — mirror this caller's messages to Important Caller"}
            className={`grid h-6 w-6 place-items-center rounded border transition-colors ${
              s.ic
                ? "border-accent-amber/40 bg-accent-amber/15 text-accent-amber"
                : "border-border text-text-dim hover:border-accent-amber/40 hover:text-accent-amber"
            }`}
          >
            <Star size={12} fill={s.ic ? "currentColor" : "none"} />
          </button>
        )}
        {onChip && (
          <ChipStyleEditor name={s.name} value={s.chip}
                           onSave={(chip) => onChip(s, chip)} />
        )}
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
      // Same three details the chat-id finder gives back, so what was added is
      // identifiable without hunting for the row.
      const bits = [r.name, r.username ? `@${r.username}` : null, `-100${r.id}`]
        .filter(Boolean).join(" · ");
      setMsg({
        ok: true,
        text: r.name ? `Added ${bits} — live now`
                     : `Added -100${r.id} — live now. Telegram would not give a `
                       + `title; it will fill in when the group next posts.`,
      });
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
  const [icf, setIcf] = useState<IcFilter>("all");
  const { data: stats } = useApi<any>("/api/forwarder/stats");
  const { data: sources } = useApi<any>("/api/forwarder/sources");
  const { data: dests } = useApi<any>("/api/forwarder/destinations");

  async function toggle(s: Source, enabled: boolean) {
    await apiSend(`/api/forwarder/sources/${encodeURIComponent(s.key)}`, "PATCH", { enabled });
    mutate("/api/forwarder/sources");
    // A channel row flips a registry service, so Settings must agree.
    if (s.kind === "channel") mutate("/api/settings/services");
  }

  // Saved straight away — the point of the picker is that what you chose is
  // what the Detections table shows, without a second "apply" step.
  async function setChip(s: Source, chip: ChipStyle | null) {
    await apiSend(`/api/forwarder/sources/${s.chat_id}/chip`, "PATCH", chip ?? {});
    mutate("/api/forwarder/sources");
    mutate("/api/forwarder/group-chips");
  }

  // Starring only marks the group: it mirrors from the next message on, and
  // does not go back over what the caller has already posted.
  async function setIc(s: Source, on: boolean) {
    await apiSend(`/api/forwarder/sources/${s.chat_id}/ic`, "PATCH", { on });
    mutate("/api/forwarder/sources");
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
  // Chat ids get pasted in whichever form Telegram showed them: -1002534554639,
  // 1002534554639 or the bare 2534554639. Comparing the digits alone matches
  // all three against the one form we store.
  const digits = needle.replace(/\D/g, "");
  const allGroups = all.filter((s) => s.kind === "group");
  const icCount = allGroups.filter((s) => s.ic).length;
  const groups = allGroups
    .filter((s) => (icf === "all" ? true : icf === "ic" ? s.ic : !s.ic))
    .filter((s) => !needle
      || s.name.toLowerCase().includes(needle)
      || (s.subtitle ?? "").toLowerCase().includes(needle)
      || (digits.length >= 4 && String(s.chat_id).includes(digits.replace(/^100/, ""))));

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
        // The number on screen, so it follows the tab and the search rather
        // than always reading the full 116.
        count={groups.length}
        controls={<>
          <FilterTabs value={icf} onChange={setIcf} options={IC_TABS} />
          <SearchBox value={q} onChange={setQ} placeholder="group name / id" />
        </>}
      >
        <p className="mb-3 text-xs text-text-dim">
          {needle
            ? `${groups.length} of ${allGroups.length} groups match "${q.trim()}".`
            : icf === "ic"
              ? `${icCount} of ${allGroups.length} callers are mirrored into Important Caller.`
              : icf === "not"
                ? `${allGroups.length - icCount} callers are not in Important Caller.`
                : `${allGroups.length} groups mirrored by the userbot, with each chat id under the name.`}
          {" "}The name is worked out on its own — the group's live Telegram
          title, otherwise the seeded one. ⭐ mirrors that caller into
          Important Caller as well, from its next message on.
        </p>
        <AddGroup />
        <TableScroll>
          <div className="space-y-1">
            {groups.map((s) => (
              <SourceRow key={s.key} s={s} onToggle={toggle} onRemove={remove} onChip={setChip} onIc={setIc} />
            ))}
            {groups.length === 0 && (
              <span className="text-xs text-text-dim">
                {needle ? "No group matches this search"
                  : icf === "ic" ? "No caller starred for Important Caller yet"
                  : icf === "not" ? "Every caller is in Important Caller"
                  : "No premium groups"}
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
