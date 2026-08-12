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
    <Card className="p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-sm font-medium text-slate-500">{label}</p>
          <p className="mt-2 text-2xl font-semibold text-slate-950">{value}</p>
          {helper ? <p className="mt-2 text-sm text-slate-500">{helper}</p> : null}
        </div>
        <span className="rounded-md bg-brand-50 p-2 text-brand-700">
          <Icon aria-hidden="true" className="h-5 w-5" />
        </span>
      </div>
    </Card>
  );
}
