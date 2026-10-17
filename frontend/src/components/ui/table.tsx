import type { ComponentProps } from "react";

import { cn } from "@/lib/utils";

/**
 * Data table: 52px rows, 12px uppercase header, hairlines between rows and a surface-muted
 * hover. For short lists (≤ 10 items) prefer tile rows: `rounded-2xl bg-surface-muted` items
 * with a 6px gap, as in the Overview "Latest traces" card.
 */
function Table({
  className,
  containerClassName,
  ...props
}: ComponentProps<"table"> & {
  /**
   * Classes for the wrapper, which scrolls sideways by default. Pass `overflow-visible` when an
   * outer element is the scroll region, so there is one keyboard-reachable scroller, not two.
   */
  containerClassName?: string;
}) {
  return (
    <div className={cn("relative w-full overflow-x-auto", containerClassName)}>
      <table
        className={cn("w-full caption-bottom border-separate border-spacing-0 text-sm", className)}
        {...props}
      />
    </div>
  );
}

function TableHeader({ className, ...props }: ComponentProps<"thead">) {
  return <thead className={cn(className)} {...props} />;
}

function TableBody({ className, ...props }: ComponentProps<"tbody">) {
  return <tbody className={cn("[&>tr:last-child>td]:border-b-0", className)} {...props} />;
}

function TableRow({ className, ...props }: ComponentProps<"tr">) {
  return (
    <tr
      className={cn(
        "group/row transition-colors",
        "[&>td]:border-b [&>td]:border-border [&>td]:transition-colors",
        "hover:[&>td]:bg-surface-muted",
        "data-[state=selected]:[&>td]:bg-surface-selected",
        className,
      )}
      {...props}
    />
  );
}

function TableHead({ className, ...props }: ComponentProps<"th">) {
  return (
    <th
      scope="col"
      className={cn(
        "h-10 border-b border-border px-4 text-left align-middle text-xs font-semibold tracking-[0.06em] whitespace-nowrap text-muted-foreground uppercase",
        className,
      )}
      {...props}
    />
  );
}

function TableCell({ className, ...props }: ComponentProps<"td">) {
  return <td className={cn("h-13 px-4 align-middle whitespace-nowrap", className)} {...props} />;
}

/**
 * The first cell of a body row when it names the row (`<th scope="row">`): body-cell height and
 * hairline, medium weight. It follows the row hover like `TableCell`.
 */
function TableRowHeader({ className, ...props }: ComponentProps<"th">) {
  return (
    <th
      scope="row"
      className={cn(
        "h-13 border-b border-border px-4 text-left align-middle text-sm font-medium whitespace-nowrap transition-colors group-last/row:border-b-0 group-hover/row:bg-surface-muted",
        className,
      )}
      {...props}
    />
  );
}

export { Table, TableBody, TableCell, TableHead, TableHeader, TableRow, TableRowHeader };
