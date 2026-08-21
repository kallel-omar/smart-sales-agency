export function Card({
  children,
  className = ""
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-[0.625rem] border border-slate-200 bg-white shadow-sm ${className}`}>
      {children}
    </section>
  );
}
