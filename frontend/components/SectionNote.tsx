"use client";

import { useEffect, useState } from "react";
import { Info } from "lucide-react";
import { Button } from "@/components/ui/button";

const KEY = "section_notes";

function readOpen(): Record<string, boolean> {
  try {
    return JSON.parse(localStorage.getItem(KEY) || "{}");
  } catch {
    return {};
  }
}

/* The paragraph under a section heading — what the section shows, what is left
 * out, which switches change it.
 *
 * It is worth reading once and then it is in the way for good: these notes grow
 * a sentence at a time as a section gains behaviour, and the longest of them
 * had reached five lines above the table it was describing. So it is folded
 * away by default and put behind an ⓘ in the section header, remembered per id
 * the same way a collapsed section is — open it once and it stays open.
 *
 * Two pieces because they live in two places: the button belongs in the
 * section's controls row, the text belongs above the table.
 *
 *   const note = useSectionNote("launchpad");
 *   <CollapsibleSection controls={<><NoteButton {...note} /> …</>}>
 *     <SectionNote open={note.open}>…</SectionNote>
 */
export function useSectionNote(id: string) {
  const [open, setOpen] = useState(false);

  // After mount: localStorage on the server would break hydration.
  useEffect(() => {
    const stored = readOpen()[id];
    if (stored !== undefined) setOpen(stored);
  }, [id]);

  function toggle() {
    const next = !open;
    setOpen(next);
    try {
      localStorage.setItem(KEY, JSON.stringify({ ...readOpen(), [id]: next }));
    } catch {}
  }

  return { open, toggle };
}

export function NoteButton({ open, toggle }: { open: boolean; toggle: () => void }) {
  return (
    <Button size="sm" variant={open ? "primary" : "outline"} onClick={toggle}
            aria-expanded={open}
            title={open ? "Hide the note" : "What this section shows"}>
      <Info size={13} />
    </Button>
  );
}

export function SectionNote({ open, children }: {
  open: boolean;
  children: React.ReactNode;
}) {
  if (!open) return null;
  return <p className="mb-3 text-xs text-text-dim">{children}</p>;
}
