import type { Credential, Route } from "@/lib/api";

import { CREDENTIAL_COLUMNS } from "./credential-columns";
import { routeUsesByCredential } from "./credential-model";
import { CredentialRow } from "./credential-row";

interface CredentialListProps {
  credentials: readonly Credential[];
  routes: readonly Route[];
  canManage: boolean;
  onNotConfigured: () => void;
}

/** Figma "Gateway — Credentials": tiles under overline column labels. */
export function CredentialList({
  credentials,
  routes,
  canManage,
  onNotConfigured,
}: CredentialListProps) {
  const uses = routeUsesByCredential(routes);

  return (
    <div className="@container flex flex-col gap-3">
      <div
        aria-hidden
        className="hidden items-center gap-3 pt-2 pr-3 pb-0.5 pl-4 text-overline text-subtle-foreground uppercase @[44rem]:flex"
      >
        <span className={CREDENTIAL_COLUMNS.name}>Name</span>
        <span className={CREDENTIAL_COLUMNS.baseUrl}>Base URL</span>
        <span className={CREDENTIAL_COLUMNS.lastUsed}>Last used</span>
        <span className={CREDENTIAL_COLUMNS.check}>Last check</span>
        <span className="w-[214px] shrink-0" />
      </div>
      <ul aria-label="Provider credentials" className="flex flex-col gap-1.5">
        {credentials.map((credential) => (
          <CredentialRow
            key={credential.id}
            credential={credential}
            canManage={canManage}
            uses={uses.get(credential.id)}
            routes={routes}
            onNotConfigured={onNotConfigured}
          />
        ))}
      </ul>
    </div>
  );
}
