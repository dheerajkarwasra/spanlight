import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentProps } from "react";

import { cn } from "@/lib/utils";

/**
 * Bento cards (20px radius). Variants:
 * - `default` white surface, hairline border, soft shadow.
 * - `hero`    dark ink card for the headline metric; use rail-* tokens for text inside
 *             (text-rail-muted-foreground, bg-rail-tile ...).
 * - `accent`  violet → fuchsia gradient with white text (a neutral raised card in dark). At most
 *             once per page.
 * - `muted`   flat surface-muted panel without shadow, for secondary groupings.
 *
 * Cards have no padding of their own: compose CardHeader / CardContent / CardFooter, or pass
 * `className="p-6"` for a single block of content.
 */
const cardVariants = cva("relative min-w-0 rounded-card", {
  variants: {
    variant: {
      default: "border border-border bg-surface text-foreground shadow-card",
      hero: "bg-hero-card text-hero-card-foreground dark:border dark:border-border",
      accent: "bg-linear-to-br from-accent-card-from to-accent-card-to text-accent-card-foreground",
      muted: "bg-surface-muted text-foreground",
    },
  },
  defaultVariants: {
    variant: "default",
  },
});

interface CardProps extends ComponentProps<"div">, VariantProps<typeof cardVariants> {}

function Card({ className, variant, ...props }: CardProps) {
  return <div className={cn(cardVariants({ variant }), className)} {...props} />;
}

/** Title block on the left, optional pills or actions on the right. */
function CardHeader({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      className={cn("flex items-start justify-between gap-4 px-6 pt-6 pb-4", className)}
      {...props}
    />
  );
}

function CardTitle({ className, ...props }: ComponentProps<"h2">) {
  return <h2 className={cn("text-card", className)} {...props} />;
}

function CardDescription({ className, ...props }: ComponentProps<"p">) {
  return <p className={cn("mt-0.5 text-xs text-muted-foreground", className)} {...props} />;
}

function CardContent({ className, ...props }: ComponentProps<"div">) {
  return <div className={cn("px-6 pb-6", className)} {...props} />;
}

function CardFooter({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      className={cn(
        "flex items-center justify-end gap-2 rounded-b-card border-t border-border px-6 py-4",
        className,
      )}
      {...props}
    />
  );
}

export { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle, cardVariants };
export type { CardProps };
