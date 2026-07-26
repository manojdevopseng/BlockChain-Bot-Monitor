"use client";

import { Users, MessageSquare, Send, AlertTriangle } from "lucide-react";
import { useApi, apiSend } from "@/lib/api";
import { mutate } from "swr";
import { PageHeader } from "@/components/PageHeader";
import { StatCard } from "@/components/StatCard";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { fmtNum } from "@/lib/utils";

export default function ForwarderPage() {
  const { data: stats } = useApi<any>("/api/forwarder/stats");
  const { data: sources } = useApi<any>("/api/forwarder/sources");
  const { data: dests } = useApi<any>("/api/forwarder/destinations");

  async function toggle(name: string, enabled: boolean) {
    await apiSend(`/api/forwarder/sources/${encodeURIComponent(name)}`, "PATCH", { enabled });
    mutate("/api/forwarder/sources");
  }

  return (
    <div className="space-y-5">
      <PageHeader title="Telegram Forwarder" subtitle="Monitoring source channels · forwarding to destination groups" />
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="Total Sources" value={stats?.total_sources ?? 0} icon={Users} tone="purple" />
        <StatCard label="Total Groups" value={stats?.total_groups ?? 0} icon={Users} tone="blue" />
        <StatCard label="Messages Today" value={fmtNum(stats?.messages_today)} icon={MessageSquare} tone="green" />
        <StatCard label="Forwarded Today" value={fmtNum(stats?.forwarded_today)} icon={Send} tone="cyan" />
      </div>
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>Source Status</CardTitle></CardHeader>
          <CardContent className="space-y-1">
            {(sources?.items ?? []).map((s: any) => (
              <div key={s.name} className="flex items-center justify-between rounded-lg border border-border-soft px-3 py-2.5">
                <div>
                  <div className="text-sm text-text">{s.name}</div>
                  <div className="text-[11px] text-text-dim">{s.subtitle}</div>
                </div>
                <div className="flex items-center gap-3">
                  <span className="text-xs text-text-muted">{fmtNum(s.today)}</span>
                  <Switch checked={s.enabled} onCheckedChange={(v) => toggle(s.name, v)} />
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Destination Groups</CardTitle></CardHeader>
          <CardContent className="space-y-1">
            {(dests?.items ?? []).map((d: any) => (
              <div key={d.group} className="flex items-center justify-between rounded-lg border border-border-soft px-3 py-2.5">
                <div>
                  <div className="flex items-center gap-2 text-sm text-text">
                    {d.group}
                    <Badge variant={d.visibility === "Public" ? "blue" : "purple"}>{d.visibility}</Badge>
                  </div>
                  <div className="text-[11px] text-text-dim">{d.purpose}</div>
                </div>
                <span className="text-xs text-text-muted">{fmtNum(d.today)}</span>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
