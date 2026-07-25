"use client";

import { Server, Clock, Activity, Database } from "lucide-react";
import { useApi } from "@/lib/api";
import { PageHeader } from "@/components/PageHeader";
import { StatCard } from "@/components/StatCard";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { fmtUptime } from "@/lib/utils";

export default function SystemPage() {
  const { data } = useApi<any>("/api/system/overview");
  const { data: svcs } = useApi<any>("/api/system/services");

  const info = [
    ["Hostname", data?.hostname],
    ["OS", data?.os],
    ["Python", data?.python],
    ["DB Backend", data?.db_backend],
    ["DB Connected", data?.db_ok ? "yes" : "no (fallback)"],
  ];

  return (
    <div className="space-y-5">
      <PageHeader title="System" subtitle="System information, health and configuration" />
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="System Status" value={data?.status ?? "—"} icon={Server} tone="green" />
        <StatCard label="Uptime" value={fmtUptime(data?.uptime_seconds ?? 0)} icon={Clock} tone="purple" />
        <StatCard label="Services" value={svcs?.items?.length ?? 0} icon={Activity} tone="blue" />
        <StatCard label="DB Backend" value={data?.db_backend ?? "—"} icon={Database} tone="cyan" />
      </div>
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>System Information</CardTitle></CardHeader>
          <CardContent className="space-y-2">
            {info.map(([k, v]) => (
              <div key={k} className="flex justify-between border-b border-border-soft py-1.5 text-sm">
                <span className="text-text-muted">{k}</span>
                <span className="font-mono text-text">{v ?? "—"}</span>
              </div>
            ))}
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Service Status</CardTitle></CardHeader>
          <CardContent className="space-y-1">
            {(svcs?.items ?? []).map((s: any) => (
              <div key={s.id} className="flex items-center justify-between rounded-lg border border-border-soft px-3 py-2 text-sm">
                <span className="text-text">{s.label}</span>
                <Badge variant={s.status === "running" && s.enabled ? "green" : "gray"}>
                  {s.enabled ? s.status : "disabled"}
                </Badge>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
