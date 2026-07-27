"use client";

import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Line, LineChart,
  Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { useTheme } from "@/lib/theme";

// Recharts needs concrete colour values (it can't read Tailwind classes), so the
// chart chrome follows the active theme explicitly.
function useChartColors() {
  const { theme } = useTheme();
  const dark = theme === "dark";
  return {
    axis: { stroke: dark ? "#334155" : "#cbd5e1", fontSize: 11 },
    grid: dark ? "#1f2937" : "#e2e8f0",
    tooltip: {
      background: dark ? "#111827" : "#ffffff",
      border: `1px solid ${dark ? "#1f2937" : "#e2e8f0"}`,
      borderRadius: 8,
      fontSize: 12,
      color: dark ? "#e5e9f0" : "#0f172a",
    },
    cursor: dark ? "#ffffff08" : "#0f172a08",
  };
}

// Recharts replays its draw animation on every data change, not just on mount.
// With the dashboard refreshing in the background that read as the chart
// flashing every few seconds, so every series below opts out.
export function AreaSpark({ data, color = "#7c5cff" }: { data: { value: number }[]; color?: string }) {
  return (
    <ResponsiveContainer width="100%" height={48}>
      <AreaChart data={data}>
        <defs>
          <linearGradient id={`g-${color}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.4} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <Area type="monotone" dataKey="value" stroke={color} strokeWidth={2}
          fill={`url(#g-${color})`} isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

export function LineSeries({
  data, keys,
}: {
  data: any[];
  keys: { key: string; color: string; label?: string }[];
}) {
  const c = useChartColors();
  return (
    <ResponsiveContainer width="100%" height={260}>
      <LineChart data={data}>
        <CartesianGrid strokeDasharray="3 3" stroke={c.grid} vertical={false} />
        <XAxis dataKey="label" {...c.axis} tickLine={false} axisLine={false} />
        <YAxis {...c.axis} tickLine={false} axisLine={false} width={30} />
        <Tooltip contentStyle={c.tooltip} />
        {keys.map((k) => (
          <Line key={k.key} type="monotone" dataKey={k.key} stroke={k.color}
            strokeWidth={2} dot={false} name={k.label || k.key}
            isAnimationActive={false} />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

export function BarSeries({ data, color = "#7c5cff" }: { data: any[]; color?: string }) {
  const c = useChartColors();
  return (
    <ResponsiveContainer width="100%" height={260}>
      <BarChart data={data}>
        <CartesianGrid strokeDasharray="3 3" stroke={c.grid} vertical={false} />
        <XAxis dataKey="label" {...c.axis} tickLine={false} axisLine={false} />
        <YAxis {...c.axis} tickLine={false} axisLine={false} width={30} />
        <Tooltip contentStyle={c.tooltip} cursor={{ fill: c.cursor }} />
        <Bar dataKey="value" fill={color} radius={[3, 3, 0, 0]} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export function Donut({
  data,
}: {
  data: { name: string; value: number; color: string }[];
}) {
  const c = useChartColors();
  return (
    <ResponsiveContainer width="100%" height={200}>
      <PieChart>
        <Pie data={data} dataKey="value" nameKey="name" innerRadius={55} outerRadius={80} paddingAngle={2}>
          {data.map((d, i) => <Cell key={i} fill={d.color} stroke="none" />)}
        </Pie>
        <Tooltip contentStyle={c.tooltip} />
      </PieChart>
    </ResponsiveContainer>
  );
}
