import { useLocation } from "@tanstack/react-router";
import { Link2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/ui/tooltip";

/**
 * Copies the address of this view (with its `span=` and `tab=`), or `href` (the peek's full-page
 * address), and confirms with a toast.
 */
export function CopyLinkButton({ href: target }: { href?: string }) {
  const current = useLocation({ select: (location) => location.href });
  const href = target ?? current;
  async function copy() {
    try {
      await navigator.clipboard.writeText(new URL(href, window.location.origin).href);
      toast.success("Link copied");
    } catch {
      toast.error("Couldn't copy the link. Copy it from the address bar.");
    }
  }
  return (
    <Tooltip content="Copy link">
      <Button size="icon-sm" aria-label="Copy link" onClick={() => void copy()}>
        <Link2 aria-hidden />
      </Button>
    </Tooltip>
  );
}
