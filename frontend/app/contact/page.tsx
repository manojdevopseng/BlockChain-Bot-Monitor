"use client";

import { useState } from "react";
import Link from "next/link";
import { CheckCircle2, Loader2, Send } from "lucide-react";
import { apiPublic } from "@/lib/api";
import { SiteChrome, SiteHeading } from "@/components/site/SiteChrome";
import { Input } from "@/components/ui/input";

/* Contact — for people who do not have an account yet.
 *
 * Anybody who does should use Support instead: that one arrives with the page
 * they were on, their plan and the state of the workers attached, which is half
 * the answer already. This form says so rather than quietly being the worse
 * route. */

export default function ContactPage() {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState("");
  const [error, setError] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    try {
      const got = await apiPublic("/api/public/contact", { name, email, message });
      setSent(got.message || "Thanks — we will be in touch.");
    } catch (err: any) {
      setError(err?.message || "Could not send that");
    } finally { setBusy(false); }
  }

  return (
    <SiteChrome>
      <SiteHeading eyebrow="Contact" title="Ask us anything"
                   lead="A person reads these. Answers come by email, usually the
                         same day." />

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        <div className="lg:col-span-2">
          {sent ? (
            <div className="flex items-start gap-2 rounded-xl border border-accent-green/30 bg-accent-green/10 p-5">
              <CheckCircle2 size={16} className="mt-0.5 text-accent-green" />
              <p className="text-sm text-text">{sent}</p>
            </div>
          ) : (
            <form onSubmit={submit}
                  className="rounded-xl border border-border-soft bg-bg-card/40 p-5">
              <label className="mb-1 block text-xs text-text-muted">Your name (optional)</label>
              <Input value={name} onChange={(e) => setName(e.target.value)} />

              <label className="mb-1 mt-3 block text-xs text-text-muted">Email</label>
              <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)}
                     placeholder="where we should reply" />

              <label className="mb-1 mt-3 block text-xs text-text-muted">Message</label>
              <textarea value={message} onChange={(e) => setMessage(e.target.value)}
                        rows={6}
                        className="w-full rounded-lg border border-border bg-bg-soft px-3 py-2 text-xs text-text placeholder:text-text-dim"
                        placeholder="What you are trying to do, and what you want to know" />

              {error && <p className="mt-3 text-xs text-accent-red">{error}</p>}

              <button type="submit" disabled={busy || !email || !message.trim()}
                      className="mt-4 inline-flex items-center gap-1.5 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50">
                {busy ? <Loader2 size={14} className="animate-spin" />
                      : <><Send size={13} /> Send</>}
              </button>
            </form>
          )}
        </div>

        <div className="space-y-4">
          <div className="rounded-xl border border-border-soft bg-bg-card/40 p-5">
            <h3 className="text-sm font-semibold text-text">Already have an account?</h3>
            <p className="mt-1.5 text-xs leading-relaxed text-text-muted">
              Use <Link href="/support" className="text-brand-soft hover:underline">Support</Link>{" "}
              inside the app instead. It attaches the page you were on, your plan
              and whether the part behind it was running — which is usually half
              the answer before anybody replies.
            </p>
          </div>
          <div className="rounded-xl border border-border-soft bg-bg-card/40 p-5">
            <h3 className="text-sm font-semibold text-text">What we cannot help with</h3>
            <p className="mt-1.5 text-xs leading-relaxed text-text-muted">
              Which token to buy, whether one will go up, or anything about a
              wallet that is not ours. We will never ask for a private key or a
              seed phrase — anybody who does is not us.
            </p>
          </div>
        </div>
      </div>
    </SiteChrome>
  );
}
