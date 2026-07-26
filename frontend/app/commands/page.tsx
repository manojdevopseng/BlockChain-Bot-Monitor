"use client";

import { Terminal, CheckCircle, Activity, Radio, AlertTriangle } from "lucide-react";
import { useApi, apiSend } from "@/lib/api";
import { mutate } from "swr";
import { PageHeader } from "@/components/PageHeader";
import { StatCard } from "@/components/StatCard";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
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

  const cols: Column<any>[] = [
    { key: "command", header: "Command", render: (r) => (
      <span className="font-mono text-xs text-brand-soft">{r.command}</span>
    )},
    { key: "description", header: "Description", render: (r) => (
      <span className="text-text-muted">{r.description}</span>
    )},
    { key: "category", header: "Category", render: (r) => <Badge variant="purple">{r.category}</Badge> },
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
    { key: "enabled", header: "Enabled", render: (r) => (
      <Switch checked={!!r.enabled} onCheckedChange={(v) => toggle(r.command, v)} />
    )},
  ];

  return (
    <div className="space-y-5">
      <PageHeader
        title="Commands"
        subtitle="Telegram slash commands, their real usage and per-command switches"
      />

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

      <Card>
        <CardHeader>
          <CardTitle>Command List</CardTitle>
          <span className="text-[11px] text-text-dim">
            switching one off also drops it from Telegram&apos;s &ldquo;/&rdquo; menu
          </span>
        </CardHeader>
        <CardContent>
          <DataTable columns={cols} rows={data?.items ?? []} empty="No commands registered" />
        </CardContent>
      </Card>
    </div>
  );
}
