import { X } from "lucide-react";
import { Dialog as SheetPrimitive } from "radix-ui";
import type { ComponentProps } from "react";

import { cn } from "@/lib/utils";

const Sheet = SheetPrimitive.Root;
const SheetTrigger = SheetPrimitive.Trigger;
const SheetClose = SheetPrimitive.Close;
const SheetTitle = SheetPrimitive.Title;
const SheetDescription = SheetPrimitive.Description;

interface SheetContentProps extends ComponentProps<typeof SheetPrimitive.Content> {
  side?: "left" | "right";
  /** Hide the built-in close button when the content renders its own. */
  hideClose?: boolean;
  overlayClassName?: string;
}

/** A floating side panel (8px inset, 28px radius) over a blurred overlay. */
function SheetContent({
  className,
  children,
  side = "left",
  hideClose = false,
  overlayClassName,
  ...props
}: SheetContentProps) {
  return (
    <SheetPrimitive.Portal>
      <SheetPrimitive.Overlay
        className={cn(
          "fixed inset-0 z-50 animate-fade-in bg-overlay backdrop-blur-[4px]",
          overlayClassName,
        )}
      />
      <SheetPrimitive.Content
        className={cn(
          "fixed inset-y-2 z-50 flex w-80 max-w-[calc(100vw-1rem)] flex-col overflow-y-auto rounded-card border border-border bg-surface text-foreground shadow-lg",
          side === "left" ? "left-2 animate-slide-in-left" : "right-2 animate-slide-in-right",
          className,
        )}
        {...props}
      >
        {children}
        {hideClose ? null : (
          <SheetPrimitive.Close
            className="absolute top-4 right-4 flex size-8 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-surface-hover hover:text-foreground"
            aria-label="Close"
          >
            <X className="size-4" aria-hidden />
          </SheetPrimitive.Close>
        )}
      </SheetPrimitive.Content>
    </SheetPrimitive.Portal>
  );
}

export { Sheet, SheetClose, SheetContent, SheetDescription, SheetTitle, SheetTrigger };
