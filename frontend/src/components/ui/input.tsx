import { CalendarDays } from "lucide-react";
import type { ComponentProps } from "react";

import { cn } from "@/lib/utils";

/** Shared field styling (Figma "Control/Input"): 46px, 14px radius, strong hairline. */
const inputClasses = cn(
  "flex h-[46px] w-full min-w-0 rounded-input border border-input bg-surface px-4 text-sm text-foreground",
  "placeholder:text-subtle-foreground",
  "transition-[color,background-color,border-color,box-shadow] duration-200 ease-out-quart",
  // Focus: the border takes the ring colour with a faint halo, no outer ring (`field-focus`).
  "field-focus",
  "disabled:cursor-not-allowed disabled:bg-surface-muted disabled:opacity-60",
  "aria-invalid:border-[1.5px] aria-invalid:border-danger",
  "file:border-0 file:bg-transparent file:text-sm file:font-medium",
  // An empty date field shows its format (dd.mm.yyyy) as a hint, not as a value.
  "data-[empty=true]:[&::-webkit-datetime-edit]:text-subtle-foreground",
);

const DATE_TYPES = new Set(["date", "datetime-local", "time", "month", "week"]);

function Input({ className, type = "text", ...props }: ComponentProps<"input">) {
  // Only a controlled field knows it is empty; an uncontrolled one keeps the default colour.
  const empty = DATE_TYPES.has(type) && props.value !== undefined ? props.value === "" : undefined;
  return (
    <input type={type} data-empty={empty} className={cn(inputClasses, className)} {...props} />
  );
}

/**
 * A date or date-time field: the browser's own picker, with its icon replaced by ours at the right
 * edge. The native indicator sits, invisible, over our icon (Chromium and Safari), so a click on
 * the icon opens the picker and a click on the text lets you type. Firefox draws an icon of its
 * own, so ours is hidden there.
 */
function DateInput({
  className,
  type = "date",
  ...props
}: Omit<ComponentProps<"input">, "type"> & { type?: "date" | "datetime-local" }) {
  return (
    <div className="relative w-full">
      <Input
        type={type}
        className={cn(
          "relative pr-11",
          "[&::-webkit-calendar-picker-indicator]:absolute [&::-webkit-calendar-picker-indicator]:inset-y-0",
          "[&::-webkit-calendar-picker-indicator]:right-0 [&::-webkit-calendar-picker-indicator]:h-auto",
          "[&::-webkit-calendar-picker-indicator]:w-11",
          "[&::-webkit-calendar-picker-indicator]:cursor-pointer [&::-webkit-calendar-picker-indicator]:opacity-0",
          className,
        )}
        {...props}
      />
      <CalendarDays
        aria-hidden
        className="pointer-events-none absolute top-1/2 right-4 size-4 -translate-y-1/2 text-muted-foreground [@supports(-moz-appearance:none)]:hidden"
      />
    </div>
  );
}

function Textarea({ className, ...props }: ComponentProps<"textarea">) {
  return (
    <textarea
      className={cn(inputClasses, "h-auto min-h-24 py-3 leading-relaxed", className)}
      {...props}
    />
  );
}

export { DateInput, Input, Textarea, inputClasses };
