import { useWorkspace } from "../../workspaces/WorkspaceProvider";
import { useAppExperience } from "../../app/AppExperience";

export function WorkspaceSwitcher() {
  const { t } = useAppExperience();
  const { workspaces, selectedWorkspaceSlug, selectWorkspace, isLoading } = useWorkspace();

  if (isLoading) {
    return <div className="h-12 animate-pulse rounded-md bg-white/5" aria-label={t("loadingWorkspaces")} />;
  }

  if (workspaces.length === 0) {
    return (
      <div className="rounded-md border border-amber-400/20 bg-amber-400/10 px-3 py-2 text-sm text-amber-300">
        {t("noWorkspace")}
      </div>
    );
  }

  return (
    <label className="block text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">
      {t("workspace")}
      <select
        className="mt-2 min-h-10 w-full rounded-md border border-white/10 bg-white/[0.06] px-3 text-sm font-medium normal-case tracking-normal text-slate-100 outline-none focus:border-blue-400"
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
