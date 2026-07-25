import { cn } from "@/lib/utils";

export type Column<T> = {
  key: string;
  header: string;
  render?: (row: T) => React.ReactNode;
  className?: string;
};

export function DataTable<T extends Record<string, any>>({
  columns, rows, empty = "No data",
}: {
  columns: Column<T>[];
  rows: T[];
  empty?: string;
}) {
  return (
    // min-w keeps columns readable on phones: the table scrolls sideways
    // instead of squeezing every cell into a tall wrapped block.
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] text-sm">
        <thead>
          <tr className="border-b border-border text-left text-[11px] uppercase tracking-wider text-text-dim">
            {columns.map((c) => (
              <th key={c.key} className={cn("px-3 py-2.5 font-medium", c.className)}>
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td colSpan={columns.length} className="px-3 py-8 text-center text-text-dim">
                {empty}
              </td>
            </tr>
          ) : (
            rows.map((row, i) => (
              <tr key={i} className="border-b border-border-soft hover:bg-bg-hover/40">
                {columns.map((c) => (
                  <td key={c.key} className={cn("px-3 py-2.5 text-text", c.className)}>
                    {c.render ? c.render(row) : row[c.key]}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
