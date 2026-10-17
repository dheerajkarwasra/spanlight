import type { CsvRowError } from "./csv-upload";

/**
 * Figma "Reconciliation / csv errors": the importer's reasons, one per row, as a real table. The
 * server never echoes a cell, so the table names the row, the column and the rule only.
 */
export function CsvErrors({ errors }: { errors: readonly CsvRowError[] }) {
  return (
    <div className="flex flex-col gap-2">
      <div className="overflow-x-auto rounded-tile border border-border">
        <table className="w-full text-left text-xs">
          <caption className="sr-only">Why the file was refused</caption>
          <thead className="bg-surface-muted text-overline text-subtle-foreground uppercase">
            <tr>
              <th scope="col" className="px-3 py-2 font-semibold">
                Row
              </th>
              <th scope="col" className="px-3 py-2 font-semibold">
                Column
              </th>
              <th scope="col" className="px-3 py-2 font-semibold">
                Problem
              </th>
            </tr>
          </thead>
          <tbody>
            {errors.map((error, index) => (
              <tr key={index} className="border-t border-border">
                <td className="px-3 py-2.5 font-mono text-muted-foreground tabular">
                  {error.row === 0 ? "File" : error.row}
                </td>
                <td className="px-3 py-2.5 font-mono text-muted-foreground">
                  {error.column ?? (
                    <>
                      <span aria-hidden>—</span>
                      <span className="sr-only">No single column</span>
                    </>
                  )}
                </td>
                <td className="px-3 py-2.5 font-medium text-foreground">{error.message}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs font-medium text-muted-foreground">
        {errors.length} {errors.length === 1 ? "error" : "errors"}. Up to 20 are listed, by row
        number; the first row after the header is row 2.
      </p>
    </div>
  );
}
