"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { CheckCircle2, Loader2, XCircle } from "lucide-react";
import { apiPublic } from "@/lib/api";
import { Button } from "@/components/ui/button";

/* The link from the confirmation email.
 *
 * It redeems itself on arrival — a page that asks somebody to press "confirm"
 * after they already pressed a link in an email is asking twice for one thing.
 * The trial starts here, so the answer says so out loud. */

function Verify() {
  const token = useSearchParams().get("token") || "";
  const [state, setState] = useState<"working" | "ok" | "bad">("working");
  const [detail, setDetail] = useState("");

  useEffect(() => {
    if (!token) { setState("bad"); setDetail("That link is missing its token."); return; }
    apiPublic("/api/account/verify", { token })
      .then((got) => {
        setState("ok");
        const days = got?.account?.days_left;
        setDetail(days ? `Your ${days}-day trial has started.` : "You are confirmed.");
      })
      .catch((err) => { setState("bad"); setDetail(err?.message || "That link did not work."); });
  }, [token]);

  return (
    <div className="grid min-h-screen place-items-center bg-bg px-4">
      <div className="w-full max-w-sm rounded-xl border border-border bg-bg-card/60 p-6 text-center backdrop-blur-sm">
        {state === "working" && (
          <>
            <Loader2 size={20} className="mx-auto animate-spin text-text-dim" />
            <p className="mt-3 text-xs text-text-dim">Confirming…</p>
          </>
        )}
        {state === "ok" && (
          <>
            <span className="mx-auto mb-3 grid h-10 w-10 place-items-center rounded-lg bg-accent-green/15 text-accent-green">
              <CheckCircle2 size={20} />
            </span>
            <h1 className="text-sm font-semibold text-text">Email confirmed</h1>
            <p className="mt-2 text-xs text-text-dim">{detail}</p>
            <Link href="/login">
              <Button variant="primary" className="mt-5 w-full justify-center">
                Sign in
              </Button>
            </Link>
          </>
        )}
        {state === "bad" && (
          <>
            <span className="mx-auto mb-3 grid h-10 w-10 place-items-center rounded-lg bg-accent-red/15 text-accent-red">
              <XCircle size={20} />
            </span>
            <h1 className="text-sm font-semibold text-text">That did not work</h1>
            <p className="mt-2 text-xs text-text-dim">{detail}</p>
            <Link href="/login">
              <Button variant="outline" className="mt-5 w-full justify-center">
                Back to sign in
              </Button>
            </Link>
          </>
        )}
      </div>
    </div>
  );
}

export default function VerifyPage() {
  // useSearchParams needs a Suspense boundary in the app router.
  return (
    <Suspense fallback={<div className="grid min-h-screen place-items-center bg-bg" />}>
      <Verify />
    </Suspense>
  );
}
