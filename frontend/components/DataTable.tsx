import { cn, rowKey } from "@/lib/utils";
import { STICKY_HEAD, TableScroll } from "@/components/TableScroll";

export type Column<T> = {
  key: string;
  header: string;
  render?: (row: T) => React.ReactNode;
  className?: string;
};

export function DataTable<T extends Record<string, any>>({
  columns, rows, empty = "No data", maxHeight,
}: {
  columns: Column<T>[];
  rows: T[];
  empty?: string;
  maxHeight?: number | false;
}) {
  return (
    <TableScroll maxHeight={maxHeight}>
      <table className="w-full min-w-[640px] text-sm">
        <thead>
          <tr className={cn(STICKY_HEAD, "border-b border-border")}>
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
              <tr key={rowKey(row, i)} className="border-b border-border-soft hover:bg-bg-hover/40">
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
    </TableScroll>
  );
}
