import type { ReactNode } from "react";

interface CardTitleProps {
  title: ReactNode;
  subtitle?: ReactNode;
  /** Pills or buttons on the right of the title. */
  actions?: ReactNode;
  /** The heading level in the page outline; cards sit under the page's h1. */
  as?: "h2" | "h3";
}

/** The Figma card header of the prompt screens: a bold 16 px title over a 12 px muted line. */
export function CardTitle({ title, subtitle, actions, as: Heading = "h2" }: CardTitleProps) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <Heading className="text-base font-bold [overflow-wrap:anywhere] text-foreground">
          {title}
        </Heading>
        {subtitle ? <p className="text-xs font-medium text-muted-foreground">{subtitle}</p> : null}
      </div>
      {actions}
    </div>
  );
}
