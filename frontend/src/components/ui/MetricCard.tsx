import type { LucideIcon } from "lucide-react";

import { Card } from "./Card";

export function MetricCard({
  icon: Icon,
  label,
  value,
  helper
}: {
  icon: LucideIcon;
  label: string;
  value: string | number;
  helper?: string;
}) {
  return (
    <Card className="relative overflow-hidden p-5">
      <span className="absolute inset-y-0 start-0 w-0.5 bg-brand-500" aria-hidden="true" />
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-sm font-medium text-slate-500">{label}</p>
          <p className="mt-2 text-2xl font-semibold tracking-tight text-slate-950">{value}</p>
          {helper ? <p className="mt-2 text-sm text-slate-500">{helper}</p> : null}
        </div>
        <span className="rounded-md bg-brand-50 p-2 text-brand-700">
          <Icon aria-hidden="true" className="h-5 w-5" />
        </span>
      </div>
    </Card>
  );
}
