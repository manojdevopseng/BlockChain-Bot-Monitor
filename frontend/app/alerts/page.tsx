"use client";

import { Bell, AlertTriangle, ShieldCheck, Eye } from "lucide-react";
import { useApi } from "@/lib/api";
import { PageHeader } from "@/components/PageHeader";
import { StatCard } from "@/components/StatCard";
import { RecentAlerts } from "@/components/RecentAlerts";

export default function AlertsPage() {
  const { data: stats } = useApi<any>("/api/alerts/stats");

  return (
    <div className="space-y-5">
      <PageHeader title="Alerts" subtitle="Real-time alerts and important events from all chains" />
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="Total Alerts" value={stats?.total ?? 0} icon={Bell} tone="red" />
        <StatCard label="High Priority" value={stats?.high ?? 0} icon={AlertTriangle} tone="red" />
        <StatCard label="Medium" value={stats?.medium ?? 0} icon={Eye} tone="amber" />
        <StatCard label="Low" value={stats?.low ?? 0} icon={ShieldCheck} tone="blue" />
      </div>
      <RecentAlerts />
    </div>
  );
}
