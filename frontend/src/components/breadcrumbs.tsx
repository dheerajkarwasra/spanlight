import { createLink } from "@tanstack/react-router";
import { ChevronRight } from "lucide-react";
import { Children, type ComponentProps, type ReactNode } from "react";

import { cn } from "@/lib/utils";

// 44 px tall on touch, 24 px from sm (WCAG 2.5.8).
const CRUMB_CLASS =
  "inline-flex min-h-11 shrink-0 items-center rounded-sm text-[13px] font-semibold text-muted-foreground transition-colors hover:text-foreground sm:min-h-6";

// A parent crumb is never the current page: drop the router's active markers, so only the
// current text crumb carries aria-current.
function CrumbAnchor({
  className,
  ref,
  "aria-current": _ariaCurrent,
  "data-status": _status,
  ...props
}: ComponentProps<"a"> & { "data-status"?: string }) {
  return <a ref={ref} {...props} className={cn(CRUMB_CLASS, className)} />;
}

const CrumbLink = createLink(CrumbAnchor);

/**
 * One parent page in `Breadcrumbs`. A typed router link, so it takes `to`, `params` and `search`
 * like `Link`; the label is its children. Never styled or announced as active.
 */
export const Crumb = ((props: ComponentProps<typeof CrumbLink>) => (
  <CrumbLink {...props} activeProps={{}} />
)) as typeof CrumbLink;

/** A parent crumb that acts in place instead of navigating, e.g. leaving an unsaved draft. */
export function CrumbButton({ className, ...props }: ComponentProps<"button">) {
  return <button type="button" {...props} className={cn(CRUMB_CLASS, className)} />;
}

interface BreadcrumbsProps {
  /** The parent pages, outermost first: one or two `Crumb`s. */
  children: ReactNode;
  /** The page itself, shown as text and truncated. */
  current: string;
  className?: string;
}

/** "Doctor › answer_ticket": the trail above a detail page's title. */
export function Breadcrumbs({ children, current, className }: BreadcrumbsProps) {
  return (
    <nav aria-label="Breadcrumb" className={className}>
      <ol className="flex min-w-0 items-center gap-1.5">
        {Children.toArray(children).map((crumb, index) => (
          <li key={index} className="flex shrink-0 items-center gap-1.5">
            {crumb}
            <ChevronRight aria-hidden className="size-3.5 text-muted-foreground" />
          </li>
        ))}
        <li className="flex min-w-0 items-center">
          <span
            aria-current="page"
            title={current}
            className="truncate text-[13px] font-semibold text-foreground"
          >
            {current}
          </span>
        </li>
      </ol>
    </nav>
  );
}
