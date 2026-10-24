import { useQueryClient } from "@tanstack/react-query";
import { getRouteApi } from "@tanstack/react-router";
import { Building2, ChevronRight } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { refreshMe } from "@/features/auth/queries";
import { orgsApi, type Membership } from "@/lib/api";
import { ROLE_LABELS } from "@/lib/permissions";

import { ONBOARDING_COPY } from "./copy";
import { NameForm } from "./name-form";
import { StepCard } from "./step-card";

const onboardingRoute = getRouteApi("/_authed/onboarding");

interface OrgStepProps {
  memberships: readonly Membership[];
}

export function OrgStep({ memberships }: OrgStepProps) {
  const navigate = onboardingRoute.useNavigate();
  const queryClient = useQueryClient();

  async function createOrg(name: string) {
    const org = await orgsApi.create(name);
    // Wait for /me to include the new membership so step 2 can verify it.
    await refreshMe(queryClient);
    toast.success(`Created ${org.name}`);
    await navigate({ search: { org: org.id } });
  }

  return (
    <div className="flex flex-col gap-8">
      <StepCard
        icon={Building2}
        title={ONBOARDING_COPY.org.cardTitle}
        description={ONBOARDING_COPY.org.cardDescription}
      >
        <NameForm
          noun="organization"
          label="Organization name"
          placeholder="Acme Inc."
          submitLabel="Continue"
          submitDescription="Creates your organization"
          onCreate={createOrg}
        />
      </StepCard>

      {memberships.length > 0 ? (
        <ExistingOrgs
          memberships={memberships}
          onPick={(orgId) => {
            void navigate({ search: { org: orgId } });
          }}
        />
      ) : null}
    </div>
  );
}

function ExistingOrgs({
  memberships,
  onPick,
}: {
  memberships: readonly Membership[];
  onPick: (orgId: string) => void;
}) {
  return (
    <section aria-labelledby="existing-orgs" className="flex flex-col gap-3">
      <h2 id="existing-orgs" className="text-label font-semibold text-muted-foreground">
        Or add a project to an existing organization
      </h2>
      <ul className="flex flex-col gap-1.5">
        {memberships.map(({ org, role }) => (
          <li key={org.id}>
            <button
              type="button"
              onClick={() => {
                onPick(org.id);
              }}
              className="flex w-full items-center gap-3 rounded-2xl border border-border bg-surface px-4 py-3 text-left text-sm shadow-xs transition-colors duration-200 hover:bg-surface-hover"
            >
              <Building2 aria-hidden className="size-4 shrink-0 text-muted-foreground" />
              <span className="min-w-0 flex-1 truncate font-medium text-foreground">
                {org.name}
              </span>
              {org.is_demo ? <Badge variant="accent">Demo</Badge> : null}
              <Badge>{ROLE_LABELS[role]}</Badge>
              <ChevronRight aria-hidden className="size-4 shrink-0 text-muted-foreground" />
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
