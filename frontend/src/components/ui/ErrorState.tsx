import { AlertTriangle } from "lucide-react";

export function ErrorState({
  title = "Something needs attention",
  description
}: {
  title?: string;
  description: string;
}) {
  return (
    <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-4 text-red-800" role="alert">
      <div className="flex gap-3">
        <AlertTriangle aria-hidden="true" className="mt-0.5 h-5 w-5 shrink-0" />
        <div>
          <h2 className="text-sm font-semibold">{title}</h2>
          <p className="mt-1 text-sm leading-6 text-red-700">{description}</p>
        </div>
      </div>
    </div>
  );
}
