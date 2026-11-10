import type { ReactNode } from "react";

import { LEGAL_FACTS } from "./legal-facts";

/** A numbered part of a legal text; the `id` is the target of the contents list. */
export function LegalSection({
  id,
  title,
  children,
}: {
  id: string;
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="flex flex-col gap-3">
      <h2 id={id} className="scroll-mt-6 text-h2 text-foreground">
        {title}
      </h2>
      {children}
    </section>
  );
}

export function LegalList({ children }: { children: ReactNode }) {
  return (
    <ul className="flex list-disc flex-col gap-1.5 pl-5 marker:text-subtle-foreground">
      {children}
    </ul>
  );
}

/**
 * A full-width table that scrolls inside its own box when it is wider than the screen, so the
 * page itself never scrolls sideways. The box is focusable so keyboard users can scroll it.
 */
export function LegalTable({
  head,
  rows,
  label,
}: {
  head: string[];
  rows: ReactNode[][];
  label: string;
}) {
  return (
    <div
      role="region"
      aria-label={label}
      tabIndex={0}
      className="overflow-x-auto rounded-tile border border-border"
    >
      <table className="w-full min-w-[34rem] text-left text-sm">
        <thead className="bg-surface-muted text-foreground">
          <tr>
            {head.map((cell) => (
              <th key={cell} scope="col" className="px-4 py-3 font-semibold">
                {cell}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            // Rows are static text in source order, so the index is a stable key.
            <tr key={rowIndex} className="border-t border-border align-top">
              {row.map((cell, cellIndex) => (
                <td key={cellIndex} className="px-4 py-3">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** An inline mark for a fact the owner has not provided yet. Nothing is guessed in its place. */
export function Missing({ what }: { what: string }) {
  return (
    <span className="inline-block rounded-full bg-warning-subtle px-2 py-0.5 align-middle text-xs font-medium whitespace-nowrap text-warning">
      <span aria-hidden="true">To be completed</span>
      <span className="sr-only">Missing: {what}</span>
    </span>
  );
}

export function Fact({ value, what, href }: { value: string | null; what: string; href?: string }) {
  if (value === null) {
    return <Missing what={what} />;
  }
  return href ? <a href={href}>{value}</a> : value;
}

/** Follows every backup claim, and flags it until the owner confirms backups run on the service. */
export function BackupsUnconfirmed() {
  return LEGAL_FACTS.backupsEnabled === true ? null : (
    <>
      {" "}
      <Missing what="backup confirmation" />
    </>
  );
}
