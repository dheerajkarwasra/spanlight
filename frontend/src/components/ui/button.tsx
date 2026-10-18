import { cva, type VariantProps } from "class-variance-authority";
import { Loader2 } from "lucide-react";
import { Slot } from "radix-ui";
import type { ComponentProps } from "react";

import { cn } from "@/lib/utils";

/**
 * Pill buttons (Figma "Button"). Variants:
 * - `primary`   ink fill (lime in the dark theme): the one main action on a screen.
 * - `secondary` white surface with a hairline and card shadow: supporting actions.
 * - `ghost`     no fill until hover: tertiary actions, toolbars.
 * - `accent`    violet fill: brand moments (onboarding, upgrade prompts).
 * - `highlight` lime fill with ink text: a celebratory or "go" action, at most one per screen.
 * - `danger`    rose fill: destructive confirmation.
 * - `link`      inline text link in the accent colour.
 * Sizes: `sm` 32px, `md` 40px, `lg` 48px; `icon-sm` / `icon` / `icon-lg` are circles.
 */
const buttonVariants = cva(
  [
    "inline-flex shrink-0 items-center justify-center gap-2 rounded-full font-semibold whitespace-nowrap",
    "transition-[background-color,color,box-shadow,transform] duration-150 ease-out-quart select-none",
    "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
    "active:scale-[0.98] disabled:pointer-events-none disabled:opacity-50",
    "[&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0",
  ],
  {
    variants: {
      variant: {
        primary: "bg-ink text-ink-foreground hover:bg-ink/90",
        secondary:
          "border border-border bg-surface text-foreground shadow-xs hover:bg-surface-muted",
        ghost: "text-foreground hover:bg-surface-hover",
        accent: "bg-accent text-accent-foreground hover:bg-accent-hover",
        highlight: "bg-lime text-lime-foreground hover:bg-lime/85",
        danger: "bg-danger text-danger-foreground hover:bg-danger-hover",
        // The dark accent is near-white, so dark links keep a faint underline to read as links.
        link: "h-auto rounded-sm px-0 text-accent underline-offset-4 hover:underline active:scale-100 dark:underline dark:decoration-border-strong",
      },
      size: {
        sm: "h-8 px-3.5 text-label",
        md: "h-10 px-[18px] text-sm [&_svg]:size-[18px]",
        lg: "h-12 px-6 text-sm [&_svg]:size-[18px]",
        "icon-sm": "size-8",
        icon: "size-10 [&_svg]:size-[18px]",
        "icon-lg": "size-12 [&_svg]:size-5",
      },
    },
    compoundVariants: [{ variant: "link", className: "h-auto px-0" }],
    defaultVariants: {
      variant: "secondary",
      size: "md",
    },
  },
);

interface ButtonProps extends ComponentProps<"button">, VariantProps<typeof buttonVariants> {
  /** Render the child element (e.g. a router `<Link>`) with button styles. */
  asChild?: boolean;
  /** Shows a spinner, disables the button and sets aria-busy. */
  loading?: boolean;
}

function Button({
  className,
  variant,
  size,
  asChild = false,
  loading = false,
  disabled,
  children,
  ...props
}: ButtonProps) {
  const classes = cn(buttonVariants({ variant, size }), className);

  if (asChild) {
    return (
      <Slot.Root className={classes} {...props}>
        {children}
      </Slot.Root>
    );
  }

  return (
    <button
      type="button"
      className={classes}
      disabled={disabled === true || loading}
      aria-busy={loading || undefined}
      {...props}
    >
      {loading ? <Loader2 className="animate-spin" aria-hidden /> : null}
      {children}
    </button>
  );
}

export { Button, buttonVariants };
export type { ButtonProps };
