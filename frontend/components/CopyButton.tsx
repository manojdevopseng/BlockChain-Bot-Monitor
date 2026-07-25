"use client";

import { useState } from "react";
import { Check, Copy } from "lucide-react";
import { cn } from "@/lib/utils";

// Copy-on-click icon. Shows a brief check on success.
export function CopyButton({ value, className }: { value: string; className?: string }) {
  const [done, setDone] = useState(false);

  async function copy(e: React.MouseEvent) {
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      // Fallback for non-secure contexts
      const ta = document.createElement("textarea");
      ta.value = value;
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand("copy"); } catch {}
      document.body.removeChild(ta);
    }
    setDone(true);
    setTimeout(() => setDone(false), 1200);
  }

  return (
    <button
      onClick={copy}
      title="Copy"
      className={cn(
        "inline-grid h-5 w-5 place-items-center rounded text-text-dim hover:bg-bg-hover hover:text-text transition-colors",
        className
      )}
    >
      {done ? <Check size={12} className="text-accent-green" /> : <Copy size={12} />}
    </button>
  );
}
