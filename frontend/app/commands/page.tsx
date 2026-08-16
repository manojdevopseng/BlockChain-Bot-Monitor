"use client";

import {
  Terminal, CheckCircle, Activity, Radio, AlertTriangle, MessageSquare,
} from "lucide-react";
import { useApi, apiSend } from "@/lib/api";
import { mutate } from "swr";
import { PageHeader } from "@/components/PageHeader";
import { CopyButton } from "@/components/CopyButton";
import { StatCard } from "@/components/StatCard";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { AdminOnly } from "@/components/AdminOnly";
import { DataTable, type Column } from "@/components/DataTable";
import { fmtNum, timeAgo } from "@/lib/utils";

export default function CommandsPage() {
  const { data: stats } = useApi<any>("/api/commands/stats");
  const { data } = useApi<any>("/api/commands");
  const running: boolean = data?.handler_running ?? false;

  async function toggle(cmd: string, enabled: boolean) {
    await apiSend(`/api/commands/${encodeURIComponent(cmd.replace("/", ""))}`, "PATCH", { enabled });
    mutate("/api/commands");
    mutate("/api/commands/stats");
  }

  // Grouped by the category each command declares, in first-seen order so the
  // General/System ones stay at the top where they have always been.
  const sections: [string, any[]][] = [];
  for (const row of (data?.items ?? [])) {
    const key = row.category || "Other";
    const found = sections.find(([c]) => c === key);
    if (found) found[1].push(row);
    else sections.push([key, [row]]);
  }

  const cols: Column<any>[] = [
    { key: "command", header: "Command", render: (r) => (
      <span className="font-mono text-xs text-brand-soft">{r.command}</span>
    )},
    { key: "description", header: "Description", render: (r) => (
      <span className="text-text-muted">{r.description}</span>
    )},
    { key: "permission", header: "Permission", render: (r) => (
      r.permission === "Group admins"
        ? <Badge variant="amber">group admins</Badge>
        : <Badge variant="gray">everyone</Badge>
    )},
    { key: "live", header: "Live", render: (r) => (
      r.live
        ? <Badge variant="green">answering</Badge>
        : <Badge variant="red">{r.enabled ? "handler down" : "off"}</Badge>
    )},
    { key: "uses_total", header: "Uses", render: (r) => fmtNum(r.uses_total ?? 0) },
    { key: "success_rate", header: "Success", render: (r) =>
      // Blank until the command has actually run — never an invented 100%.
      r.success_rate == null
        ? <span className="text-text-dim">—</span>
        : <span className={r.success_rate >= 100 ? "text-accent-green" : "text-accent-amber"}>
            {r.success_rate}%
          </span>
    },
    { key: "last_used", header: "Last used", render: (r) => (
      <span className="text-text-muted">{r.last_used ? timeAgo(r.last_used) : "never"}</span>
    )},
    // The switch is the operator's: these are one bot's commands, not a copy
    // per account. A customer sees what the state is, not a control that would
    // answer 403 — and would be turning the command off for everybody if it
    // did not.
    { key: "enabled", header: "Enabled", render: (r) => (
      <AdminOnly fallback={
        <Badge variant={r.enabled ? "green" : "gray"}>{r.enabled ? "on" : "off"}</Badge>
      }>
        <Switch checked={!!r.enabled} onCheckedChange={(v) => toggle(r.command, v)} />
      </AdminOnly>
    )},
  ];

  return (
    <div className="space-y-5">
      <PageHeader
        title="Commands"
        subtitle="Telegram slash commands, their real usage and per-command switches"
      />

      {stats?.chat_id && (
        <Card>
          <CardContent className="flex flex-wrap items-center gap-2 pt-4 text-sm">
            <MessageSquare size={15} className="shrink-0 text-brand-soft" />
            <span className="text-text-muted">Answers only in chat</span>
            <code className="rounded bg-bg-soft px-2 py-0.5 font-mono text-xs text-brand-soft">
              {stats.chat_id}
            </code>
            <CopyButton value={String(stats.chat_id)} />
            <span className="text-xs text-text-dim">
              — har dusre group aur DM me bot chup rehta hai, &ldquo;/&rdquo; menu bhi
              nahi dikhta. Badalna ho to <code>COMMAND_CHAT_ID</code> in <code>.env</code>.
            </span>
          </CardContent>
        </Card>
      )}

      {!running && (
        <Card className="border-accent-amber/40">
          <CardContent className="pt-4 text-sm">
            <div className="flex items-start gap-2">
              <AlertTriangle size={16} className="mt-0.5 shrink-0 text-accent-amber" />
              <div>
                <div>Command handler is not running — the bot will not reply on Telegram.</div>
                <div className="mt-1 text-xs text-text-muted">
                  {stats?.handler_enabled === false
                    ? 'Turn "Bot Commands" on in Settings → Bots.'
                    : "Set TELEGRAM_BOT_TOKEN in .env (get one from @BotFather), then restart the API."}
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="Commands" value={stats?.total ?? 0} icon={Terminal} tone="purple" />
        <StatCard label="Enabled" value={stats?.enabled ?? 0} icon={CheckCircle} tone="green" />
        <StatCard label="Total Uses" value={fmtNum(stats?.uses_total ?? 0)} icon={Activity} tone="blue" />
        <StatCard
          label="Handler"
          value={running ? "running" : "stopped"}
          icon={Radio}
          tone={running ? "green" : "red"}
        />
      </div>

      {/* One card per category rather than one long table. The categories
          come from COMMAND_SPEC, so a new group of commands gets its own
          section the day it is written — RSI Controller is nine of them and
          would otherwise be scattered through everything else alphabetically. */}
      {sections.map(([category, rows]) => (
        <Card key={category}>
          <CardHeader>
            <CardTitle>{category}</CardTitle>
            <span className="text-[11px] text-text-dim">
              {rows.filter((r: any) => r.enabled).length}/{rows.length} enabled
              {category === sections[0]?.[0] && " — switching one off also drops it "
                + "from Telegram's “/” menu"}
            </span>
          </CardHeader>
          <CardContent>
            <DataTable columns={cols} rows={rows} empty="No commands here" />
          </CardContent>
        </Card>
      ))}
      {sections.length === 0 && (
        <Card><CardContent className="pt-4 text-sm text-text-dim">
          No commands registered
        </CardContent></Card>
      )}
    </div>
  );
}
