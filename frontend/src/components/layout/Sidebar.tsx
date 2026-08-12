import { NavLink } from "react-router-dom";

import { departmentItems, navigationItems } from "./navigation";
import { WorkspaceSwitcher } from "./WorkspaceSwitcher";

export function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <aside className="flex h-full flex-col bg-slate-950 text-white">
      <div className="border-b border-white/10 px-5 py-5">
        <p className="text-lg font-semibold tracking-tight">HIRI</p>
        <p className="mt-1 text-xs text-slate-400">AI Business Operating System</p>
      </div>
      <div className="border-b border-white/10 px-4 py-4">
        <div className="rounded-lg bg-white p-3 text-slate-950">
          <WorkspaceSwitcher />
        </div>
      </div>
      <nav className="flex-1 overflow-y-auto px-3 py-4" aria-label="Primary navigation">
        <div className="space-y-1">
          {navigationItems.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              end={item.path === "/app"}
              onClick={onNavigate}
              className={({ isActive }) =>
                `flex min-h-11 items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition ${
                  isActive ? "bg-white text-slate-950" : "text-slate-300 hover:bg-white/10 hover:text-white"
                }`
              }
            >
              <item.icon aria-hidden="true" className="h-5 w-5" />
              <span>{item.label}</span>
            </NavLink>
          ))}
        </div>
        <div className="mt-6 border-t border-white/10 pt-4">
          <p className="px-3 text-xs font-semibold uppercase tracking-wide text-slate-500">Departments</p>
          <div className="mt-3 space-y-1">
            {departmentItems.map((item) => (
              <div key={item.label} className="flex items-center gap-3 rounded-md px-3 py-2 text-sm text-slate-400">
                <item.icon aria-hidden="true" className="h-4 w-4" />
                <span>{item.label}</span>
              </div>
            ))}
          </div>
        </div>
      </nav>
    </aside>
  );
}
