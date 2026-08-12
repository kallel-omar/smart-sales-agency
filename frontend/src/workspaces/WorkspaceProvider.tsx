import { useQuery } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useMemo, useState } from "react";

import { useAuth } from "../auth/AuthProvider";
import { apiClient } from "../lib/api";
import { queryKeys } from "../lib/queryKeys";
import { workspaceStorage } from "../lib/storage";
import type { WorkspaceRead } from "../types/api";

interface WorkspaceContextValue {
  workspaces: WorkspaceRead[];
  selectedWorkspace: WorkspaceRead | null;
  selectedWorkspaceSlug: string | null;
  isLoading: boolean;
  error: Error | null;
  selectWorkspace: (slug: string) => void;
}

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

export function WorkspaceProvider({ children }: { children: React.ReactNode }) {
  const { token, status } = useAuth();
  const [selectedSlug, setSelectedSlug] = useState<string | null>(() => workspaceStorage.get());

  const workspacesQuery = useQuery({
    queryKey: queryKeys.workspaces,
    queryFn: () => apiClient.workspaces(token as string),
    enabled: status === "authenticated" && Boolean(token)
  });

  const workspaces = workspacesQuery.data ?? [];

  useEffect(() => {
    if (status !== "authenticated") {
      setSelectedSlug(null);
      return;
    }
    if (workspaces.length === 0) {
      workspaceStorage.clear();
      setSelectedSlug(null);
      return;
    }
    const persisted = selectedSlug && workspaces.some((workspace) => workspace.slug === selectedSlug);
    if (!persisted) {
      const nextSlug = workspaces[0].slug;
      workspaceStorage.set(nextSlug);
      setSelectedSlug(nextSlug);
    }
  }, [status, selectedSlug, workspaces]);

  const selectWorkspace = (slug: string) => {
    workspaceStorage.set(slug);
    setSelectedSlug(slug);
  };

  const selectedWorkspace =
    workspaces.find((workspace) => workspace.slug === selectedSlug) ?? workspaces[0] ?? null;

  const value = useMemo(
    () => ({
      workspaces,
      selectedWorkspace,
      selectedWorkspaceSlug: selectedWorkspace?.slug ?? null,
      isLoading: workspacesQuery.isLoading,
      error: workspacesQuery.error,
      selectWorkspace
    }),
    [workspaces, selectedWorkspace, workspacesQuery.isLoading, workspacesQuery.error]
  );

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace() {
  const context = useContext(WorkspaceContext);
  if (!context) {
    throw new Error("useWorkspace must be used within WorkspaceProvider");
  }
  return context;
}
