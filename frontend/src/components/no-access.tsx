import { Lock } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { Card } from "@/components/ui/card";
import { rolesWith, type Permission } from "@/lib/permissions";

interface NoAccessProps {
  /** The page as a reader names it, e.g. "The audit log". */
  page: string;
  /** The permission the page needs; the copy names the roles that hold it. */
  permission: Permission;
}

/**
 * The page a role can't read, reached by a direct link (Figma "Settings v4 — Access states", B).
 * The server refuses the data anyway; this says why instead of showing an error.
 */
export function NoAccess({ page, permission }: NoAccessProps) {
  return (
    <Card>
      <EmptyState
        icon={Lock}
        headingLevel={2}
        title="You don't have access to this page"
        description={`${page} is available to ${rolesWith(permission)}. Ask an admin or owner of this organization if you need it.`}
      />
    </Card>
  );
}
