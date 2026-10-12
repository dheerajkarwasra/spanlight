import { Kbd } from "@/components/ui/kbd";
import { cn } from "@/lib/utils";

/** Figma "Key chip": the bordered key hint next to work-mode actions and options. */
export function KeyChip({ children, className }: { children: string; className?: string }) {
  return (
    <Kbd
      className={cn(
        "h-6 min-w-6 rounded-tooltip border border-border-strong px-[7px] text-xs text-foreground",
        className,
      )}
    >
      {children}
    </Kbd>
  );
}
