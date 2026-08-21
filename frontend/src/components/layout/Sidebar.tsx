import { Layers3 } from "lucide-react";
import { NavLink } from "react-router-dom";

import { useAppExperience } from "../../app/AppExperience";
import { navigationItems } from "./navigation";
import { WorkspaceSwitcher } from "./WorkspaceSwitcher";

export function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const { t } = useAppExperience();
  return <aside className="app-sidebar flex h-full flex-col">
    <div className="flex h-[4.25rem] items-center border-b border-white/10 px-5"><img className="h-8 w-8 shrink-0" src="/hiri-logo.svg" alt="HIRI logo" /><div className="ms-3"><p className="text-[1rem] font-bold tracking-[0.2em] text-white">HIRI</p><p className="mt-0.5 text-[9px] font-medium text-slate-500">{t("operatingSystem")}</p></div></div>
    <div className="border-b border-white/10 px-4 py-4"><WorkspaceSwitcher /></div>
    <nav className="flex-1 overflow-y-auto px-3 py-5" aria-label={t("primaryNavigation")}><p className="px-3 text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-600">{t("operatingSystem")}</p><div className="mt-3 space-y-1">{navigationItems.map((item) => <NavLink key={item.path} to={item.path} end={item.path === "/app"} onClick={onNavigate} className={({ isActive }) => `group flex min-h-11 items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition ${isActive ? "app-nav-active" : "text-slate-400 hover:bg-white/[0.06] hover:text-white"}`}><item.icon aria-hidden="true" className="h-[1.1rem] w-[1.1rem] shrink-0" /><span>{t(item.key)}</span></NavLink>)}</div>
      <div className="mt-8 border-t border-white/10 pt-5"><p className="px-3 text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-600">{t("department")}</p><div className="mt-3 flex items-center gap-3 rounded-md border border-white/[0.07] bg-white/[0.03] px-3 py-3"><span className="grid h-8 w-8 place-items-center rounded-md bg-blue-500/15 text-blue-400"><Layers3 className="h-4 w-4" /></span><div><p className="text-xs font-semibold text-slate-200">{t("salesDepartment")}</p><p className="mt-0.5 text-[10px] text-emerald-400">{t("operational")}</p></div></div></div>
    </nav>
    <div className="border-t border-white/10 px-5 py-4"><p className="text-[10px] leading-4 text-slate-600">HIRI · {t("humanGovernedAiOperations")}</p></div>
  </aside>;
}
