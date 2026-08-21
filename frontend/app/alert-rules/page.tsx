"use client";

import { useEffect, useState } from "react";
import { BellRing, Clock, Filter, HelpCircle, Send, Plus, X } from "lucide-react";
import { useApi, apiSend } from "@/lib/api";
import { PageHeader } from "@/components/PageHeader";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";

/* Alert Rules — the one page where a customer decides what their own phone does.
 *
 * Everything else in the app is the operator's box, shared: the same launches,
 * the same panels, the same numbers for everybody. These rules are not shared,
 * and they are the difference between "there is a dashboard" and "it tells me
 * when something I care about happens".
 *
 * Saved on change rather than behind a Save button. Every control here is one
 * field; a form that has to be submitted is a form people leave half-set. */

type Rules = {
  enabled: boolean;
  feeds: Record<string, boolean>;
  chains: string[];
  launchpads: string[];
  min_followers: number;
  with_x_only: boolean;
  strong_dev_buy_only: boolean;
  keywords: string[];
  keywords_only: boolean;
  watch_handles: string[];
  watch_only: boolean;
  skip_handles: string[];
  quiet_from: string;
  quiet_to: string;
  mode: string;
  digest_minutes: number;
  daily_cap: number;
};

export default function AlertRulesPage() {
  const { data, mutate: refresh } = useApi<any>("/api/alert-rules");
  const [rules, setRules] = useState<Rules | null>(null);
  const [saving, setSaving] = useState(false);
  const [note, setNote] = useState("");

  // Local copy so a switch moves the instant it is pressed; the server's answer
  // replaces it when it lands. Without this every toggle waits a round trip.
  useEffect(() => { if (data?.rules) setRules(data.rules); }, [data?.rules]);

  if (!data || !rules) {
    return (
      <div className="space-y-5">
        <PageHeader title="Alert Rules" subtitle="What reaches your Telegram, and when" />
        <div className="h-64 animate-pulse rounded-xl border border-border bg-bg-card" />
      </div>
    );
  }

  const plan = data.plan ?? {};
  // `paid` already meant "this plan can be sent Telegram", which since the
  // trial lost Telegram is the same question as "may this account change any
  // of these rules". Shown to a trial rather than hidden — the page is the
  // clearest description of what a paid plan does — but every control on it
  // is inert, because saving a rule that can never fire is a lie the page
  // would be telling.
  const paid: boolean = !!plan.telegram_alerts;
  const ro = !paid;          // read-only
  const linked: boolean = !!data.telegram_linked;

  async function save(patch: Partial<Rules>) {
    setRules((r) => (r ? { ...r, ...patch } as Rules : r));
    setSaving(true);
    setNote("");
    try {
      const r = await apiSend<any>("/api/alert-rules", "PATCH", patch);
      setRules(r.rules);
    } catch (e: any) {
      setNote(e?.message || "could not save");
      refresh();
    } finally {
      setSaving(false);
    }
  }

  async function test() {
    setNote("");
    try {
      const r = await apiSend<any>("/api/alert-rules/test", "POST");
      setNote(`Sent to your ${r.to}.`);
    } catch (e: any) {
      setNote(e?.message || "could not send");
    }
  }

  return (
    <div className="space-y-5">
      <PageHeader
        title="Alert Rules"
        subtitle="What reaches your Telegram, and when"
      >
        {saving ? <span className="text-xs text-text-dim">saving…</span> : null}
        <Button size="sm" variant="outline" onClick={test} disabled={!linked || !paid}>
          <Send size={13} /> Send a test
        </Button>
      </PageHeader>

      {/* The two reasons an account hears nothing, said before anything else on
          the page — every control below is pointless while either is true. */}
      {!paid && (
        <Card className="border-accent-amber/40">
          <CardContent className="pt-4 text-sm">
            The {plan.label} plan has dashboard alerts only. These rules are saved
            and will start working the moment a paid plan does.
          </CardContent>
        </Card>
      )}
      {paid && !linked && (
        <Card className="border-accent-amber/40">
          <CardContent className="pt-4 text-sm">
            No Telegram chat is connected yet — rules with nowhere to send.
            Connect one from your <a className="text-brand-soft underline" href="/profile">Profile</a>.
          </CardContent>
        </Card>
      )}
      {note && (
        <Card><CardContent className="pt-4 text-sm text-text-muted">{note}</CardContent></Card>
      )}

      <Card>
        <CardHeader className="flex-row items-center justify-between">
          <CardTitle className="flex items-center gap-2">
            <BellRing size={15} /> Feeds
          </CardTitle>
          <div className="flex items-center gap-2 text-xs text-text-muted">
            All alerts
            <Switch disabled={ro} checked={rules.enabled}
                    onCheckedChange={(v) => save({ enabled: v })} />
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-xs text-text-dim">
            Every feed starts off. Turn on what you want told, then narrow it
            with the filters below — the launch feed alone is a few hundred a day.
          </p>
          {(data.feeds ?? []).map((f: any) => (
            <label key={f.id}
                   className="flex items-center justify-between gap-3 rounded-lg border border-border-soft px-3 py-2">
              <span className="text-sm text-text">{f.label}</span>
              <Switch disabled={ro} checked={!!rules.feeds?.[f.id]}
                      onCheckedChange={(v) =>
                        save({ feeds: { ...rules.feeds, [f.id]: v } })} />
            </label>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Filter size={15} /> Filters</CardTitle>
        </CardHeader>
        <CardContent className="space-y-5">
          <Chips disabled={ro} label="Chains" all={data.chains ?? []} chosen={rules.chains}
                 onChange={(chains) => save({ chains })} />
          <Chips disabled={ro} label="Launchpads" all={data.launchpads ?? []}
                 chosen={rules.launchpads} emptyMeansAll
                 onChange={(launchpads) => save({ launchpads })} />

          <div className="grid gap-4 sm:grid-cols-2">
            <NumberField label="Minimum followers" value={rules.min_followers}
                         hint="0 keeps every launch, account or not"
                         onSave={(min_followers) => save({ min_followers })} />
            <NumberField label="Most alerts a day" value={rules.daily_cap}
                         hint={`up to ${plan.daily_cap ?? 0}; the rest stay on the dashboard`}
                         onSave={(daily_cap) => save({ daily_cap })} />
          </div>

          <Check label="Only launches that carry an X account"
                 checked={rules.with_x_only}
                 onChange={(v) => save({ with_x_only: v })} />
          <Check label="Only a Strong Signal dev buy"
                 hint="the deployer bought a real amount of their own token"
                 checked={rules.strong_dev_buy_only}
                 onChange={(v) => save({ strong_dev_buy_only: v })} />
          <Check label="Only when one of my keywords is in the bio"
                 checked={rules.keywords_only}
                 onChange={(v) => save({ keywords_only: v })} />
          <Check label="Only accounts on my watch list"
                 checked={rules.watch_only}
                 onChange={(v) => save({ watch_only: v })} />
        </CardContent>
      </Card>

      <div className="grid gap-5 lg:grid-cols-3">
        <WordList title="My keywords" words={rules.keywords} placeholder="robinhood"
                  hint="Matched whole-word in the account's bio. A match is marked on the alert."
                  onChange={(keywords) => save({ keywords })} />
        <WordList title="Watch list" words={rules.watch_handles} placeholder="@someone"
                  hint="Accounts you always want to hear about."
                  onChange={(watch_handles) => save({ watch_handles })} />
        <WordList title="Skip list" words={rules.skip_handles} placeholder="@someone"
                  hint="Accounts you never want to hear about."
                  onChange={(skip_handles) => save({ skip_handles })} />
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Clock size={15} /> Delivery</CardTitle>
        </CardHeader>
        <CardContent className="space-y-5">
          <div>
            <div className="mb-2 text-xs font-medium text-text-muted">How they arrive</div>
            <div className="flex flex-wrap gap-2">
              {(data.modes ?? []).map((m: string) => (
                <button key={m} onClick={() => save({ mode: m })}
                        className={cn("rounded-lg border px-3 py-1.5 text-xs transition-colors",
                          rules.mode === m
                            ? "border-brand bg-brand/15 text-brand-soft"
                            : "border-border text-text-muted hover:bg-bg-hover")}>
                  {m === "instant" ? "One message each" : "A digest"}
                </button>
              ))}
              {rules.mode === "digest" && (data.digest_choices ?? []).map((n: number) => (
                <button key={n} onClick={() => save({ digest_minutes: n })}
                        className={cn("rounded-lg border px-3 py-1.5 text-xs transition-colors",
                          rules.digest_minutes === n
                            ? "border-brand bg-brand/15 text-brand-soft"
                            : "border-border text-text-muted hover:bg-bg-hover")}>
                  every {n}m
                </button>
              ))}
            </div>
          </div>

          <div>
            <div className="mb-2 text-xs font-medium text-text-muted">
              Quiet hours <span className="text-text-dim">(IST — leave blank for none)</span>
            </div>
            <div className="flex items-center gap-2">
              <Input type="time" value={rules.quiet_from} className="h-8 w-32 text-xs"
                     onChange={(e) => save({ quiet_from: e.target.value })} />
              <span className="text-xs text-text-dim">to</span>
              <Input type="time" value={rules.quiet_to} className="h-8 w-32 text-xs"
                     onChange={(e) => save({ quiet_to: e.target.value })} />
            </div>
            <p className="mt-1.5 text-[11px] text-text-dim">
              Nothing is sent inside these hours, and nothing is held back to be
              sent after them — the dashboard has them all either way.
            </p>
          </div>

          <div className="text-xs text-text-muted">
            Sent today: <b>{data.sent_today ?? 0}</b> of {rules.daily_cap}
          </div>
        </CardContent>
      </Card>

      <WhyPanel />
    </div>
  );
}

/* "Why am I not getting alerts?" — the same question the support inbox gets,
 * answered from the launches that actually happened rather than from the
 * settings above. A list of filters says what you set; this says what it did. */
function WhyPanel() {
  const [open, setOpen] = useState(false);
  const { data } = useApi<any>(open ? "/api/alert-rules/why" : null);

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle className="flex items-center gap-2">
          <HelpCircle size={15} /> Why am I not getting alerts?
        </CardTitle>
        <Button size="sm" variant={open ? "primary" : "outline"}
                onClick={() => setOpen((v) => !v)}>
          {open ? "Hide" : "Check"}
        </Button>
      </CardHeader>
      {open && (
        <CardContent className="space-y-4">
          {!data ? (
            <div className="h-24 animate-pulse rounded-lg bg-bg-soft" />
          ) : (
            <>
              {data.blockers?.length > 0 ? (
                <div className="space-y-1.5">
                  {data.blockers.map((b: string) => (
                    <div key={b} className="flex items-start gap-2 text-sm text-accent-amber">
                      <span>•</span><span>{b}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-accent-green">
                  Nothing is stopping delivery — Telegram is connected, a feed is
                  on, and the day&apos;s cap has room.
                </p>
              )}

              <div className="rounded-lg border border-border-soft p-3">
                <p className="text-sm text-text">
                  Of the last <b>{data.sample}</b> launches, your rules would have
                  sent <b>{data.would_send}</b>.
                  {data.delay_seconds ? (
                    <span className="text-text-muted">
                      {" "}Your plan sends each one {data.delay_seconds}s after it happens.
                    </span>
                  ) : null}
                </p>

                {data.rejected_for?.length > 0 && (
                  <div className="mt-3">
                    <div className="mb-1.5 text-[11px] font-medium uppercase tracking-wider text-text-muted">
                      What turned the rest away
                    </div>
                    <div className="space-y-1">
                      {data.rejected_for.slice(0, 6).map((r: any) => (
                        <div key={r.reason} className="flex items-baseline gap-2 text-xs">
                          <Badge variant="gray">{r.count}</Badge>
                          <span className="text-text-muted">{r.reason}</span>
                        </div>
                      ))}
                    </div>
                    <p className="mt-2 text-[11px] text-text-dim">
                      The top line is the filter doing the most work — loosen that
                      one first.
                    </p>
                  </div>
                )}

                {data.recent_matches?.length > 0 && (
                  <div className="mt-3">
                    <div className="mb-1.5 text-[11px] font-medium uppercase tracking-wider text-text-muted">
                      Recent launches that fit
                    </div>
                    <div className="space-y-1 text-xs text-text-muted">
                      {data.recent_matches.map((m: any, i: number) => (
                        <div key={`${m.symbol}-${i}`}>
                          <b className="text-text">{m.symbol}</b>
                          {m.handle ? ` · @${m.handle}` : ""}
                          {m.followers ? ` · ${m.followers} followers` : ""}
                          {m.dev_buy_eth ? ` · ${m.dev_buy_eth} Ξ` : ""}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>

              {!data.delivery_running && (
                <p className="text-xs text-accent-red">
                  Alert delivery is not running on the server right now. That is
                  ours to fix — please raise a ticket from Support.
                </p>
              )}
            </>
          )}
        </CardContent>
      )}
    </Card>
  );
}

/* ── the small pieces ─────────────────────────────────────────────────────── */

function Chips({ label, all, chosen, onChange, emptyMeansAll = false, disabled = false }: {
  disabled?: boolean;
  label: string;
  all: { id: string; label: string }[];
  chosen: string[];
  onChange: (next: string[]) => void;
  emptyMeansAll?: boolean;
}) {
  const has = (id: string) => chosen.includes(id);
  return (
    <div>
      <div className="mb-2 text-xs font-medium text-text-muted">
        {label}
        {emptyMeansAll && chosen.length === 0 ? (
          <span className="ml-1.5 text-text-dim">— none picked, so all of them</span>
        ) : null}
      </div>
      <div className="flex flex-wrap gap-2">
        {all.map((item) => (
          <button key={item.id}
                  disabled={disabled}
                  onClick={() => onChange(has(item.id)
                    ? chosen.filter((c) => c !== item.id)
                    : [...chosen, item.id])}
                  className={cn("rounded-lg border px-3 py-1.5 text-xs transition-colors",
                    has(item.id)
                      ? "border-brand bg-brand/15 text-brand-soft"
                      : "border-border text-text-muted hover:bg-bg-hover")}>
            {item.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function Check({ label, hint, checked, onChange }: {
  label: string; hint?: string; checked: boolean; onChange: (v: boolean) => void;
}) {
  return (
    <label className="flex items-start justify-between gap-3">
      <span className="text-sm text-text">
        {label}
        {hint ? <span className="block text-[11px] text-text-dim">{hint}</span> : null}
      </span>
      <Switch checked={checked} onCheckedChange={onChange} />
    </label>
  );
}

// Committed on blur or Enter, not on every keystroke: saving per character
// would be one request per digit typed.
function NumberField({ label, value, hint, onSave }: {
  label: string; value: number; hint?: string; onSave: (v: number) => void;
}) {
  const [draft, setDraft] = useState(String(value ?? 0));
  useEffect(() => { setDraft(String(value ?? 0)); }, [value]);
  const commit = () => {
    const n = parseInt(draft || "0", 10);
    if (Number.isFinite(n) && n !== value) onSave(n);
  };
  return (
    <div>
      <div className="mb-1.5 text-xs font-medium text-text-muted">{label}</div>
      <Input value={draft} inputMode="numeric" className="h-8 w-full text-xs"
             onChange={(e) => setDraft(e.target.value.replace(/\D/g, ""))}
             onBlur={commit}
             onKeyDown={(e) => e.key === "Enter" && commit()} />
      {hint ? <p className="mt-1 text-[11px] text-text-dim">{hint}</p> : null}
    </div>
  );
}

function WordList({ title, words, hint, placeholder, onChange }: {
  title: string; words: string[]; hint: string; placeholder: string;
  onChange: (next: string[]) => void;
}) {
  const [draft, setDraft] = useState("");
  const add = () => {
    const word = draft.trim().replace(/^@/, "");
    if (!word) return;
    if (!words.some((w) => w.toLowerCase() === word.toLowerCase())) {
      onChange([...words, word]);
    }
    setDraft("");
  };
  return (
    <Card>
      <CardHeader><CardTitle>{title}</CardTitle></CardHeader>
      <CardContent className="space-y-3">
        <p className="text-[11px] text-text-dim">{hint}</p>
        <div className="flex gap-2">
          <Input value={draft} placeholder={placeholder} className="h-8 text-xs"
                 onChange={(e) => setDraft(e.target.value)}
                 onKeyDown={(e) => e.key === "Enter" && add()} />
          <Button size="sm" variant="outline" onClick={add}><Plus size={13} /></Button>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {words.length === 0 ? (
            <span className="text-[11px] text-text-dim">nothing yet</span>
          ) : words.map((w) => (
            <Badge key={w} variant="gray">
              <span className="mr-1">{w}</span>
              <button onClick={() => onChange(words.filter((x) => x !== w))}
                      aria-label={`Remove ${w}`}
                      className="text-text-dim hover:text-accent-red">
                <X size={10} />
              </button>
            </Badge>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
