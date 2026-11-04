import { Check, Copy } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Button, type ButtonProps } from "@/components/ui/button";
import { Tooltip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

interface CopyButtonProps extends Omit<ButtonProps, "onClick" | "children"> {
  value: string;
  /** Accessible label, e.g. "Copy API key". */
  label?: string;
  /** Show the label next to the icon instead of only in the tooltip. */
  showLabel?: boolean;
  /** Text-only labelled buttons (`showLabel`) set this false; the label then reads "Copied". */
  showIcon?: boolean;
  /**
   * `default` for light surfaces. `ink` for dark code blocks and hero cards: a translucent
   * white pill with white text and a lime check.
   */
  tone?: "default" | "ink";
}

const RESET_AFTER_MS = 1500;

const INK_CLASSES =
  "bg-rail-tile text-rail-foreground hover:bg-rail-tile-hover hover:text-rail-foreground focus-visible:outline-lime";

export function CopyButton({
  value,
  label = "Copy",
  showLabel = false,
  showIcon = true,
  tone = "default",
  variant = "ghost",
  size,
  className,
  ...props
}: CopyButtonProps) {
  const [copied, setCopied] = useState(false);
  const timeoutRef = useRef<number | undefined>(undefined);

  useEffect(() => {
    return () => {
      window.clearTimeout(timeoutRef.current);
    };
  }, []);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.clearTimeout(timeoutRef.current);
      timeoutRef.current = window.setTimeout(() => {
        setCopied(false);
      }, RESET_AFTER_MS);
    } catch {
      toast.error("Couldn't copy to the clipboard. Select the text and copy it manually.");
    }
  }

  const checkClass = tone === "ink" ? "text-lime" : "text-success";
  const icon = copied ? <Check aria-hidden className={checkClass} /> : <Copy aria-hidden />;
  const button = (
    <Button
      variant={variant}
      size={size ?? (showLabel ? "sm" : "icon-sm")}
      onClick={handleCopy}
      aria-label={showLabel ? undefined : label}
      className={cn(tone === "ink" && INK_CLASSES, className)}
      {...props}
    >
      {showIcon ? icon : null}
      {showLabel ? (copied ? "Copied" : label) : null}
      <span className="sr-only" aria-live="polite">
        {copied ? "Copied to clipboard" : ""}
      </span>
    </Button>
  );

  if (showLabel) {
    return button;
  }
  return <Tooltip content={copied ? "Copied" : label}>{button}</Tooltip>;
}
