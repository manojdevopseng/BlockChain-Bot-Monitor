"use client";

import { useEffect, useState } from "react";

/**
 * A trailing debounce, for values that feed an SWR key.
 *
 * Every search box on this dashboard drives a request: typing straight into the
 * key fires one per character, and the list empties and refills each time. The
 * same 250ms wait had been written four times — twice as this hook and twice
 * inline — which is three copies too many for something every page needs to
 * agree on.
 */
export function useDebounced(value: string, ms = 250): string {
  const [out, setOut] = useState(value.trim());
  useEffect(() => {
    const t = setTimeout(() => setOut(value.trim()), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return out;
}
