import { useQuery } from "@tanstack/react-query";
import { Bot, ShieldCheck } from "lucide-react";

import { useAuth } from "../auth/AuthProvider";
import { DefinitionList, label } from "../components/operator/OperatorDisplay";
import { Badge } from "../components/ui/Badge";
import { Card } from "../components/ui/Card";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingState } from "../components/ui/LoadingState";
import { PageHeader } from "../components/ui/PageHeader";
import { apiClient } from "../lib/api";
import { queryKeys } from "../lib/queryKeys";
import { useWorkspace } from "../workspaces/WorkspaceProvider";
import { useAppExperience } from "../app/AppExperience";

export function WorkforcePage() {
  const { t } = useAppExperience();
  const { token } = useAuth();
  const { selectedWorkspace, selectedWorkspaceSlug } = useWorkspace();
  const query = useQuery({
    queryKey: queryKeys.operatorWorkforce(selectedWorkspaceSlug ?? "none"),
    queryFn: () => apiClient.operatorWorkforce(token as string, selectedWorkspaceSlug as string),
    enabled: Boolean(token && selectedWorkspaceSlug)
  });

  return (
    <div>
      <PageHeader eyebrow={t("workforce")} title={t("aiEmployees")} description={t("workforceDescription")} action={<Badge tone="blue">{selectedWorkspace?.name ?? t("noWorkspace")}</Badge>} />
      <div className="p-5 sm:p-7">
        {query.isLoading ? <LoadingState label={t("loadingWorkforce")} /> : null}
        {query.error ? <ErrorState description={t("unableLoadWorkforce")} /> : null}
        {!query.isLoading && !query.error && !query.data?.length ? <EmptyState icon={Bot} title={t("noEmployees")} description={t("noEmployeesDescription")} /> : null}
        <div className="grid gap-5 xl:grid-cols-2">
          {query.data?.map((employee) => (
            <Card key={employee.id} className="p-5">
              <div className="flex items-start justify-between gap-4">
                <div><h2 className="font-semibold text-slate-950">{employee.name}</h2><p className="mt-1 text-sm text-slate-500">{label(employee.role_key)} · {label(employee.department)}</p></div>
                <Badge tone={employee.active ? "green" : "slate"}>{employee.active ? t("active") : t("inactive")}</Badge>
              </div>
              <div className="mt-5 space-y-4">
                {employee.capabilities.map((capability) => (
                  <div key={capability.assignment_id} className="rounded-lg border border-slate-200 p-4">
                    <div className="flex items-center justify-between gap-3"><div className="flex items-center gap-2"><ShieldCheck className="h-4 w-4 text-brand-600" /><h3 className="text-sm font-semibold">{label(capability.key)}</h3></div><Badge tone={capability.active ? "green" : "slate"}>{capability.active ? t("enabled") : t("disabled")}</Badge></div>
                    {capability.tool_access.length ? <div className="mt-3 space-y-3">{capability.tool_access.map((access) => <DefinitionList key={`${capability.assignment_id}-${access.integration_account_id}-${access.action_type}`} values={{ action: label(access.action_type), autonomy: label(access.autonomy_level), integration: access.external_account_id || access.provider, provider: label(access.provider), access_active: access.active }} />)}</div> : <p className="mt-3 text-sm text-slate-500">{t("noToolAccess")}</p>}
                  </div>
                ))}
                {!employee.capabilities.length ? <p className="text-sm text-slate-500">{t("noCapabilities")}</p> : null}
              </div>
            </Card>
          ))}
        </div>
      </div>
    </div>
  );
}
