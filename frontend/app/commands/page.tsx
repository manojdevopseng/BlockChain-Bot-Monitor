"use client";

import { Terminal, CheckCircle, Activity, Users } from "lucide-react";
import { useApi, apiSend } from "@/lib/api";
import { mutate } from "swr";
import { PageHeader } from "@/components/PageHeader";
import { StatCard } from "@/components/StatCard";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { DataTable, type Column } from "@/components/DataTable";
import { fmtNum } from "@/lib/utils";

export default function CommandsPage() {
  const { data: stats } = useApi<any>("/api/commands/stats");
  const { data } = useApi<any>("/api/commands");

  async function toggle(cmd: string, enabled: boolean) {
    await apiSend(`/api/commands/${encodeURIComponent(cmd.replace("/", ""))}`, "PATCH", { enabled });
    mutate("/api/commands");
  }

  const cols: Column<any>[] = [
    { key: "command", header: "Command", render: (r) => <span className="font-mono text-xs text-brand-soft">{r.command}</span> },
    { key: "description", header: "Description", render: (r) => <span className="text-text-muted">{r.description}</span> },
    { key: "category", header: "Category", render: (r) => <Badge variant="purple">{r.category}</Badge> },
    { key: "permission", header: "Permission", render: (r) => <Badge variant="blue">{r.permission}</Badge> },
    { key: "usage_24h", header: "Usage 24h", render: (r) => fmtNum(r.usage_24h) },
    { key: "success_rate", header: "Success", render: (r) => <span className="text-accent-green">{r.success_rate}%</span> },
    { key: "enabled", header: "Enabled", render: (r) => (
      <Switch checked={r.enabled} onCheckedChange={(v) => toggle(r.command, v)} />
    )},
  ];

  return (
    <div className="space-y-5">
      <PageHeader title="Commands" subtitle="Manage bot commands, permissions and usage" />
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="Total Commands" value={stats?.total ?? 0} icon={Terminal} tone="purple" />
        <StatCard label="Enabled" value={stats?.enabled ?? 0} icon={CheckCircle} tone="green" />
        <StatCard label="Uses (24h)" value={fmtNum(stats?.uses_24h)} icon={Activity} tone="blue" delta={24.6} />
        <StatCard label="Users (24h)" value="2,341" icon={Users} tone="cyan" />
      </div>
      <Card>
        <CardHeader><CardTitle>Command List</CardTitle></CardHeader>
        <CardContent><DataTable columns={cols} rows={data?.items ?? []} /></CardContent>
      </Card>
    </div>
  );
}
