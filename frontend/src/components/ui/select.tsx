import { Check, ChevronDown } from "lucide-react";
import { Select as SelectPrimitive } from "radix-ui";
import type { ComponentProps, ReactNode } from "react";

import { cn } from "@/lib/utils";

const Select = SelectPrimitive.Root;
const SelectGroup = SelectPrimitive.Group;
const SelectValue = SelectPrimitive.Value;

function SelectTrigger({
  className,
  children,
  ...props
}: ComponentProps<typeof SelectPrimitive.Trigger>) {
  return (
    <SelectPrimitive.Trigger
      className={cn(
        "flex h-[46px] w-full items-center justify-between gap-2 rounded-input border border-input bg-surface px-4 text-sm text-foreground",
        "transition-[color,background-color,border-color,box-shadow] duration-200 ease-out-quart",
        "field-focus",
        "disabled:cursor-not-allowed disabled:opacity-50 data-[placeholder]:text-subtle-foreground",
        "[&>span]:truncate",
        className,
      )}
      {...props}
    >
      {children}
      <SelectPrimitive.Icon asChild>
        <ChevronDown className="size-4 shrink-0 text-muted-foreground" aria-hidden />
      </SelectPrimitive.Icon>
    </SelectPrimitive.Trigger>
  );
}

function SelectContent({
  className,
  children,
  position = "popper",
  ...props
}: ComponentProps<typeof SelectPrimitive.Content>) {
  return (
    <SelectPrimitive.Portal>
      <SelectPrimitive.Content
        position={position}
        sideOffset={4}
        className={cn(
          "relative z-50 max-h-(--radix-select-content-available-height) min-w-(--radix-select-trigger-width) animate-scale-in overflow-hidden",
          "rounded-popover border border-border bg-surface shadow-lg",
          className,
        )}
        {...props}
      >
        <SelectPrimitive.Viewport className="p-1.5">{children}</SelectPrimitive.Viewport>
      </SelectPrimitive.Content>
    </SelectPrimitive.Portal>
  );
}

interface SelectItemProps extends ComponentProps<typeof SelectPrimitive.Item> {
  /** Shown before the label in the list only, not in the trigger (a colour dot). */
  leading?: ReactNode;
  /** Shown after the label in the list only, not in the trigger (the stored value in mono). */
  trailing?: ReactNode;
}

function SelectItem({ className, children, leading, trailing, ...props }: SelectItemProps) {
  return (
    <SelectPrimitive.Item
      className={cn(
        "relative flex h-9 w-full cursor-default items-center rounded-md pr-9 pl-3 text-sm outline-none select-none",
        "focus:bg-surface-muted data-[disabled]:pointer-events-none data-[disabled]:opacity-50",
        "data-[state=checked]:font-semibold",
        className,
      )}
      {...props}
    >
      {leading}
      <SelectPrimitive.ItemText>{children}</SelectPrimitive.ItemText>
      {trailing}
      <SelectPrimitive.ItemIndicator className="absolute right-3 flex items-center">
        <Check className="size-4 text-muted-foreground" aria-hidden />
      </SelectPrimitive.ItemIndicator>
    </SelectPrimitive.Item>
  );
}

export { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue };
