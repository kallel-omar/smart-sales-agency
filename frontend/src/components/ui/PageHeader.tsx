export function PageHeader({
  eyebrow,
  title,
  description,
  action
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-4 border-b border-slate-200 bg-white px-5 py-6 sm:flex-row sm:items-center sm:justify-between sm:px-7">
      <div>
        {eyebrow ? <p className="flex items-center gap-2 text-xs font-semibold text-brand-600"><span className="h-px w-5 bg-brand-500" aria-hidden="true" />{eyebrow}</p> : null}
        <h1 className="mt-2 text-2xl font-semibold tracking-[-0.025em] text-slate-950">{title}</h1>
        {description ? <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-600">{description}</p> : null}
      </div>
      {action ? <div className="flex shrink-0 items-center gap-2">{action}</div> : null}
    </div>
  );
}
