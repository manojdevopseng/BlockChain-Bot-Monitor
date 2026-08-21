"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/* /home was the front page for one afternoon. The front page is now the root,
 * where a visitor expects it — this keeps the older link working rather than
 * answering 404 to anybody who bookmarked it. */

export default function HomeAlias() {
  const router = useRouter();
  useEffect(() => { router.replace("/"); }, [router]);
  return <div className="min-h-screen bg-bg" />;
}
