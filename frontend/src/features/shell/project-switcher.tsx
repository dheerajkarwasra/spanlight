import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { Building2, Check, ChevronDown, ChevronsUpDown, FolderPlus, Plus } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { useMe } from "@/features/auth";
import { orgsApi, queryKeys } from "@/lib/api";
import { cn } from "@/lib/utils";

import {
  useCurrentOrg,
  useOrgProjectsQuery,
  useProjectParams,
  useProjectQuery,
} from "./project-context";

interface ProjectSwitcherProps {
  /**
   * `rail`: the translucent tile on the dark rail (Figma "Project switcher").
   * `appbar`: the compact overline + name trigger in the mobile app bar.
   */
  variant: "rail" | "appbar";
}

/** Switches project or organization. The trigger shows the current project. */
export function ProjectSwitcher({ variant }: ProjectSwitcherProps) {
  const me = useMe();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { orgId, projectId } = useProjectParams();
  const org = useCurrentOrg();
  const projectQuery = useProjectQuery();
  const projectsQuery = useOrgProjectsQuery(orgId);

  async function switchOrg(nextOrgId: string) {
    try {
      const projects = await queryClient.query({
        queryKey: queryKeys.org(nextOrgId).projects,
        queryFn: () => orgsApi.projects(nextOrgId),
      });
      const first = projects[0];
      if (first) {
        await navigate({
          to: "/$orgId/$projectId/overview",
          params: { orgId: nextOrgId, projectId: first.id },
        });
      } else {
        await navigate({ to: "/onboarding", search: { org: nextOrgId } });
      }
    } catch {
      toast.error("Couldn't open that organization. Try again.");
    }
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className={cn(
          "flex min-w-0 items-center text-left transition-colors duration-200",
          variant === "rail"
            ? "w-full flex-col items-stretch gap-0.5 rounded-2xl bg-rail-tile px-3.5 py-3 hover:bg-rail-tile-hover focus-visible:outline-lime data-[state=open]:bg-rail-tile-hover"
            : // Stretch, not items-start: a fit-content row sizes to the whole name and never truncates.
              "flex-col items-stretch rounded-md",
        )}
        aria-label={`Project ${projectQuery.data?.name ?? ""}. Switch project or organization`}
      >
        <span
          className={cn(
            "flex items-center gap-2 text-overline uppercase",
            variant === "rail" ? "text-rail-subtle-foreground" : "text-muted-foreground",
          )}
        >
          Project
          {org?.is_demo ? (
            <Badge variant="lime" size="sm" className="tracking-normal normal-case">
              Demo
            </Badge>
          ) : null}
        </span>
        <span className="flex min-w-0 items-center justify-between gap-1">
          {projectQuery.data ? (
            <span
              title={projectQuery.data.name}
              className={cn(
                "min-w-0 truncate text-sm font-semibold",
                variant === "rail" ? "text-rail-foreground" : "text-foreground",
              )}
            >
              {projectQuery.data.name}
            </span>
          ) : (
            <Skeleton
              className={cn(
                "my-0.5 h-4 w-28",
                variant === "rail" && "from-rail-tile via-rail-tile-hover to-rail-tile",
              )}
            />
          )}
          {variant === "rail" ? (
            <ChevronsUpDown className="size-3.5 shrink-0 text-rail-subtle-foreground" aria-hidden />
          ) : (
            <ChevronDown className="size-3.5 shrink-0 text-foreground" aria-hidden />
          )}
        </span>
      </DropdownMenuTrigger>

      <DropdownMenuContent align="start" className="w-72">
        <DropdownMenuLabel>Projects in {org?.name ?? "this organization"}</DropdownMenuLabel>
        <DropdownMenuGroup>
          {projectsQuery.data?.map((project) => (
            <DropdownMenuItem
              key={project.id}
              onSelect={() => {
                void navigate({
                  to: "/$orgId/$projectId/overview",
                  params: { orgId, projectId: project.id },
                });
              }}
            >
              <span className="flex-1 truncate">{project.name}</span>
              {project.id === projectId ? <Check aria-label="Current project" /> : null}
            </DropdownMenuItem>
          ))}
          <DropdownMenuItem
            onSelect={() => {
              void navigate({ to: "/onboarding", search: { org: orgId } });
            }}
          >
            <FolderPlus aria-hidden />
            New project
          </DropdownMenuItem>
        </DropdownMenuGroup>

        <DropdownMenuSeparator />

        <DropdownMenuLabel>Organizations</DropdownMenuLabel>
        <DropdownMenuGroup>
          {me.memberships.map(({ org: memberOrg }) => (
            <DropdownMenuItem
              key={memberOrg.id}
              onSelect={() => {
                if (memberOrg.id !== orgId) {
                  void switchOrg(memberOrg.id);
                }
              }}
            >
              <Building2 aria-hidden />
              <span className="flex-1 truncate">{memberOrg.name}</span>
              {memberOrg.is_demo ? (
                <Badge variant="accent" size="sm">
                  Demo
                </Badge>
              ) : null}
              {memberOrg.id === orgId ? <Check aria-label="Current organization" /> : null}
            </DropdownMenuItem>
          ))}
          <DropdownMenuItem
            onSelect={() => {
              void navigate({ to: "/onboarding", search: {} });
            }}
          >
            <Plus aria-hidden />
            New organization
          </DropdownMenuItem>
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
