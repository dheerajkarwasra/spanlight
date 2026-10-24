import { useNavigate } from "@tanstack/react-router";
import { FolderOpen, Keyboard, LogOut, Monitor, Moon, Search, Settings, Sun } from "lucide-react";
import { useState } from "react";

import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "@/components/ui/command";
import { Kbd } from "@/components/ui/kbd";
import { visibleSettingsNav, type SettingsPath } from "@/features/settings/settings-nav-items";
import { useTheme } from "@/lib/theme";

import { HUBS } from "./nav-items";
import { useCurrentRole, useOrgProjectsQuery, useProjectParams } from "./project-context";
import { useSignOut } from "./use-sign-out";

const TRACE_ID_PATTERN = /^[0-9a-f]{32}$/i;

/** Extra words a settings page matches, for the tasks people name instead of the page. */
const SETTINGS_KEYWORDS: Partial<Record<SettingsPath, string[]>> = {
  "/$orgId/$projectId/settings/channels": ["alert channels", "slack", "webhook", "email"],
};

interface CommandPaletteProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onOpenShortcuts: () => void;
}

export function CommandPalette({ open, onOpenChange, onOpenShortcuts }: CommandPaletteProps) {
  const navigate = useNavigate();
  const params = useProjectParams();
  const projectsQuery = useOrgProjectsQuery(params.orgId);
  const role = useCurrentRole();
  const { setTheme } = useTheme();
  const signOut = useSignOut();
  const [query, setQuery] = useState("");
  const trimmed = query.trim().toLowerCase();

  function run(action: () => void) {
    onOpenChange(false);
    setQuery("");
    action();
  }

  return (
    <CommandDialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) {
          setQuery("");
        }
      }}
      title="Command palette"
      description="Search for pages, projects and actions"
    >
      <CommandInput
        value={query}
        onValueChange={setQuery}
        placeholder="Type a command, page, or trace ID…"
      />
      <CommandList>
        <CommandEmpty>No matching commands.</CommandEmpty>

        {TRACE_ID_PATTERN.test(trimmed) ? (
          <CommandGroup heading="Trace">
            <CommandItem
              value={`open trace ${trimmed}`}
              onSelect={() => {
                run(() => {
                  void navigate({
                    to: "/$orgId/$projectId/traces/$traceId",
                    params: { ...params, traceId: trimmed },
                  });
                });
              }}
            >
              <Search aria-hidden />
              Open trace <span className="font-mono text-xs">{trimmed}</span>
            </CommandItem>
          </CommandGroup>
        ) : null}

        <CommandGroup heading="Go to">
          {HUBS.flatMap((hub) =>
            hub.tabs.map(({ label, to, icon: Icon, shortcut, keywords }) => (
              <CommandItem
                key={to}
                value={`go to ${label} ${hub.label}`}
                keywords={keywords}
                onSelect={() => {
                  run(() => {
                    void navigate({ to, params });
                  });
                }}
              >
                <Icon aria-hidden />
                <span className="flex-1">
                  {label}
                  {label === hub.label ? null : (
                    <span className="ml-2 text-muted-foreground">{hub.label}</span>
                  )}
                </span>
                {shortcut ? (
                  <span className="flex gap-1" aria-hidden>
                    {shortcut.split(" ").map((key) => (
                      <Kbd key={key}>{key}</Kbd>
                    ))}
                  </span>
                ) : null}
              </CommandItem>
            )),
          )}
        </CommandGroup>

        <CommandGroup heading="Settings">
          {visibleSettingsNav(role).flatMap((group) =>
            group.items.map(({ label, to }) => (
              <CommandItem
                key={to}
                value={`go to settings ${label}`}
                keywords={SETTINGS_KEYWORDS[to]}
                onSelect={() => {
                  run(() => {
                    void navigate({ to, params });
                  });
                }}
              >
                <Settings aria-hidden />
                {label}
              </CommandItem>
            )),
          )}
        </CommandGroup>

        {projectsQuery.data && projectsQuery.data.length > 1 ? (
          <CommandGroup heading="Switch project">
            {projectsQuery.data
              .filter((project) => project.id !== params.projectId)
              .map((project) => (
                <CommandItem
                  key={project.id}
                  value={`switch project ${project.name}`}
                  onSelect={() => {
                    run(() => {
                      void navigate({
                        to: "/$orgId/$projectId/overview",
                        params: { orgId: params.orgId, projectId: project.id },
                      });
                    });
                  }}
                >
                  <FolderOpen aria-hidden />
                  {project.name}
                </CommandItem>
              ))}
          </CommandGroup>
        ) : null}

        <CommandSeparator />

        <CommandGroup heading="Preferences">
          <CommandItem
            value="theme light"
            onSelect={() => {
              run(() => {
                setTheme("light");
              });
            }}
          >
            <Sun aria-hidden />
            Use light theme
          </CommandItem>
          <CommandItem
            value="theme dark"
            onSelect={() => {
              run(() => {
                setTheme("dark");
              });
            }}
          >
            <Moon aria-hidden />
            Use dark theme
          </CommandItem>
          <CommandItem
            value="theme system"
            onSelect={() => {
              run(() => {
                setTheme("system");
              });
            }}
          >
            <Monitor aria-hidden />
            Match system theme
          </CommandItem>
          <CommandItem
            value="keyboard shortcuts help"
            onSelect={() => {
              run(onOpenShortcuts);
            }}
          >
            <Keyboard aria-hidden />
            <span className="flex-1">Keyboard shortcuts</span>
            <Kbd aria-hidden>?</Kbd>
          </CommandItem>
          <CommandItem
            value="sign out log out"
            onSelect={() => {
              run(() => {
                signOut.mutate();
              });
            }}
          >
            <LogOut aria-hidden />
            Sign out
          </CommandItem>
        </CommandGroup>
      </CommandList>
    </CommandDialog>
  );
}
