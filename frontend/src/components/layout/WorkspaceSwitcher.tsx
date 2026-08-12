import { useWorkspace } from "../../workspaces/WorkspaceProvider";

export function WorkspaceSwitcher() {
  const { workspaces, selectedWorkspaceSlug, selectWorkspace, isLoading } = useWorkspace();

  if (isLoading) {
    return <div className="h-10 rounded-md bg-slate-100" aria-label="Loading workspaces" />;
  }

  if (workspaces.length === 0) {
    return (
      <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
        No workspace
      </div>
    );
  }

  return (
    <label className="block text-xs font-semibold uppercase tracking-wide text-slate-500">
      Workspace
      <select
        className="mt-2 min-h-10 w-full rounded-md border border-slate-200 bg-white px-3 text-sm font-medium text-slate-950 shadow-sm"
        value={selectedWorkspaceSlug ?? ""}
        onChange={(event) => selectWorkspace(event.target.value)}
      >
        {workspaces.map((workspace) => (
          <option key={workspace.id} value={workspace.slug}>
            {workspace.name}
          </option>
        ))}
      </select>
    </label>
  );
}
