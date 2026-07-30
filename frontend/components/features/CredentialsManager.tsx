"use client";

/**
 * Editor for the .env keys the backend marks editable (`envfile.EDITABLE`).
 *
 * Lives here rather than inside a page because two pages use it: Settings owns
 * the GMGN credentials, AI numbers and detection thresholds, while RPC Monitor
 * owns the endpoint URLs — those belong next to the live connection status, not
 * three cards down a settings column.
 *
 * `only` / `exclude` pick which backend groups a given placement renders, so
 * both pages read the same endpoint and neither needs its own copy of the save
 * logic, the masking rules or the per-kind controls.
 */

import { useState } from "react";
import { Brain, Check, Fuel, KeyRound, Link2, Loader2, Radio, SlidersHorizontal } from "lucide-react";
import { mutate } from "swr";
import { useApi, apiSend } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";

const ENDPOINT = "/api/settings/credentials";

const GROUP_META: Record<string, { icon: typeof KeyRound; blurb: string }> = {
  "GMGN Credentials": {
    icon: KeyRound,
    blurb:
      "GMGN ka fingerprint expire ho jaye to yahin naya paste karo — .env me purana " +
      "replace hoke save hoga aur turant apply bhi ho jayega (restart nahi chahiye).",
  },
  "AI": {
    icon: Brain,
    blurb:
      "Agent ke numbers. Ye .env me likhe jate hain aur turant apply hote hain — " +
      "restart nahi chahiye, aur server restart hone par bhi bane rehte hain.",
  },
  "RPC Endpoints — Ethereum": {
    icon: Radio,
    blurb:
      "Quota khatam ho ya 429 aaye to rotation 1 → 2 → 3 → 1 chalti hai, teeno refuse " +
      "karein to Telegram par alert jata hai. #2/#3 dusre provider ke lena — same " +
      "account ka doosra URL usi quota par marta hai.",
  },
  "RPC Endpoints — Robinhood Chain": {
    icon: Radio,
    blurb:
      "Quota khatam ho ya 429 aaye to rotation 1 → 2 → 3 → 1 chalti hai, teeno refuse " +
      "karein to Telegram par alert jata hai. #2/#3 dusre provider ke lena — same " +
      "account ka doosra URL usi quota par marta hai.",
  },
  "RPC Endpoints — Solana": {
    icon: Radio,
    blurb:
      "SOL ke 2 alag kaam, 2 alag pair: WebSocket (Helius) launch discovery + " +
      "market-cap watch dono carry karta hai — logsSubscribe support karne wala " +
      "provider chahiye, Alchemy nahi karta. HTTP (Alchemy) sirf premium-group " +
      "me mile SOL address ko verify karta hai, discovery se koi lena-dena nahi. " +
      "Dono pair me 2-way failover hai — pehla fail to dusra, dusra fail to pehla.",
  },
  "RPC Endpoints — ETH Gas Fees": {
    icon: Fuel,
    blurb:
      "ETH Gas Fees ka apna key — ye feature har watched pair par ek subscription " +
      "rakhta hai aur har buy ka receipt padhta hai, to shared key par ye new-pair " +
      "detection ke compute units kha jata hai. Khaali chhodo to ETH #1 share karega.",
  },
  "Robinhood Detection Sources": {
    icon: Link2,
    blurb:
      "Robinhood detector kin sources ko sunta hai. Har ek apni alag WS subscription " +
      "hai — jo source off hai usse launch hua token kabhi dikhega hi nahi, matching " +
      "SOL ticker bhi fire nahi hogi. Settings → Bots ka “SOL to RBH” in sab ka master hai.",
  },
  "Detection Tuning": {
    icon: SlidersHorizontal,
    blurb:
      "Detection thresholds. Ye bhi .env me likhe jate hain, to server restart hone " +
      "par bhi bane rehte hain.",
  },
};

function EnvField({ envKey, meta }: { envKey: string; meta: any }) {
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");

  async function save(value: string | boolean) {
    setBusy(true);
    setError("");
    try {
      await apiSend(`${ENDPOINT}/${envKey}`, "PUT", { value });
      setDraft("");
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
      mutate(ENDPOINT);
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  }

  // A switch writes on click; text/number fields need an explicit Save so a
  // half-typed number is never applied.
  if (meta.kind === "bool") {
    return (
      <div className="rounded-lg border border-border-soft px-3 py-2.5">
        <div className="flex items-center justify-between gap-3">
          <span className="text-sm text-text">{meta.label}</span>
          <Switch
            checked={meta.value === "true"}
            disabled={busy}
            onCheckedChange={(v) => save(v)}
          />
        </div>
        {meta.help && <p className="mt-1 text-[11px] text-text-dim">{meta.help}</p>}
        {error && <p className="mt-1 text-[11px] text-accent-red">{error}</p>}
      </div>
    );
  }

  // A URL is free text, not a number, and an endpoint may be emptied — that is
  // how a fallback is removed once its provider is dropped.
  const isText = meta.kind === "text" || meta.kind === "wss";
  // Only a fallback may be cleared. Emptying a primary would blind that chain
  // outright, and there is no reason to do it from here — replacing it is a
  // Save, not a Clear. Matched with `includes` so _FALLBACK2 counts too.
  const clearable = meta.kind === "wss" && meta.set && envKey.includes("_FALLBACK");

  return (
    <div>
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="text-xs text-text-muted">{meta.label}</span>
        <Badge variant={meta.set ? "green" : "gray"}>{meta.set ? "set" : "empty"}</Badge>
      </div>
      {meta.set && (
        <div className="mb-1 truncate font-mono text-[11px] text-text-dim" title={meta.value}>
          current: {meta.value || "—"}
        </div>
      )}
      <div className="flex gap-2">
        <Input
          value={draft}
          inputMode={isText ? undefined : "decimal"}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={
            meta.kind === "wss" ? "wss://…" : isText ? `New ${meta.label}…` : meta.value || "value"
          }
          onKeyDown={(e) => e.key === "Enter" && draft.trim() && save(draft.trim())}
          className="font-mono text-xs"
        />
        <Button
          variant="primary"
          size="sm"
          disabled={busy || !draft.trim()}
          onClick={() => save(draft.trim())}
        >
          {busy ? <Loader2 size={14} className="animate-spin" /> : saved ? <Check size={14} /> : "Save"}
        </Button>
        {clearable && (
          <Button
            variant="ghost"
            size="sm"
            disabled={busy}
            title="Remove this endpoint"
            onClick={() => save("")}
          >
            Clear
          </Button>
        )}
      </div>
      {meta.help && <p className="mt-1 text-[11px] text-text-dim">{meta.help}</p>}
      {error && <p className="mt-1 text-[11px] text-accent-red">{error}</p>}
    </div>
  );
}

// A backend group matches an `only`/`exclude` entry either exactly ("AI") or as
// a family ("RPC Endpoints" catches "RPC Endpoints — Ethereum" and its Robinhood
// and Solana siblings) — so the RPC Monitor page doesn't have to name all three
// chain sub-groups, and a fourth chain added later is picked up automatically.
function matchesGroup(group: string, entry: string): boolean {
  return group === entry || group.startsWith(`${entry} — `);
}

// One card per group of .env fields. `only` and `exclude` let a group be placed
// where it belongs — the AI numbers sit with the AI switches, the endpoints with
// the RPC status table — without a second copy of this component.
export function CredentialsManager({
  only,
  exclude,
}: {
  only?: string | string[];
  exclude?: string | string[];
}) {
  const { data } = useApi<any>(ENDPOINT);
  const items: Record<string, any> = data?.items ?? {};
  const want = only === undefined ? [] : Array.isArray(only) ? only : [only];
  const skip = exclude === undefined ? [] : Array.isArray(exclude) ? exclude : [exclude];

  const groups: Record<string, [string, any][]> = {};
  for (const [key, meta] of Object.entries(items)) {
    const group = meta.group ?? "Other";
    if (want.length && !want.some((w) => matchesGroup(group, w))) continue;
    if (skip.some((s) => matchesGroup(group, s))) continue;
    (groups[group] ||= []).push([key, meta]);
  }

  return (
    <>
      {Object.entries(groups).map(([group, fields]) => {
        const Icon = GROUP_META[group]?.icon ?? KeyRound;
        return (
          <Card key={group}>
            <CardHeader>
              <CardTitle className="flex items-center gap-2"><Icon size={14} /> {group}</CardTitle>
            </CardHeader>
            <CardContent>
              {GROUP_META[group]?.blurb && (
                <p className="mb-3 text-xs text-text-dim">{GROUP_META[group].blurb}</p>
              )}
              <div className="space-y-3">
                {fields.map(([key, meta]) => (
                  <EnvField key={key} envKey={key} meta={meta} />
                ))}
              </div>
            </CardContent>
          </Card>
        );
      })}
    </>
  );
}
