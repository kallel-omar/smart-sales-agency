export function Avatar({ name, email }: { name?: string | null; email: string }) {
  const label = name || email;
  const initials = label
    .split(/\s|@/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");

  return (
    <div
      aria-label={label}
      className="flex h-9 w-9 items-center justify-center rounded-full bg-slate-800 text-xs font-semibold text-white"
      title={label}
    >
      {initials || "U"}
    </div>
  );
}
