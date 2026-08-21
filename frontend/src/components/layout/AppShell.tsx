import { Languages, LogOut, Menu, Moon, Sun } from "lucide-react";
import { useState } from "react";
import { Outlet, useLocation } from "react-router-dom";

import { type AppLocale, useAppExperience } from "../../app/AppExperience";
import { useAuth } from "../../auth/AuthProvider";
import { useWorkspace } from "../../workspaces/WorkspaceProvider";
import { Avatar } from "../ui/Avatar";
import { Sidebar } from "./Sidebar";

const routeKeys: [string, string][] = [
  ["/app/inbox", "inbox"], ["/app/workforce", "workforce"], ["/app/work-items", "workItems"],
  ["/app/approvals", "approvals"], ["/app/analytics", "analytics"]
];

export function AppShell() {
  return <AuthenticatedShell />;
}

function AuthenticatedShell() {
  const [mobileOpen, setMobileOpen] = useState(false);
  const { logout, user } = useAuth();
  const { selectedWorkspace } = useWorkspace();
  const { direction, locale, setLocale, setTheme, t, theme } = useAppExperience();
  const { pathname } = useLocation();
  const pageKey = routeKeys.find(([path]) => pathname.startsWith(path))?.[1] ?? "dashboard";

  return (
    <div className="hiri-app min-h-screen" data-theme={theme} dir={direction} lang={locale}>
      <div className="app-sidebar-frame hidden lg:fixed lg:inset-y-0 lg:flex lg:w-64 lg:flex-col"><Sidebar /></div>
      {mobileOpen ? <div className="fixed inset-0 z-50 lg:hidden" role="dialog" aria-modal="true" aria-label={t("dashboard")}><button className="absolute inset-0 bg-slate-950/70" aria-label={t("closeNavigation")} onClick={() => setMobileOpen(false)} /><div className="relative h-full w-72 max-w-[86vw] shadow-2xl"><Sidebar onNavigate={() => setMobileOpen(false)} /></div></div> : null}

      <div className="app-content lg:ms-64">
        <header className="app-topbar sticky top-0 z-40 flex h-[4.25rem] items-center justify-between gap-4 border-b px-4 sm:px-6">
          <div className="flex min-w-0 items-center gap-3">
            <button type="button" className="app-icon-button lg:hidden" aria-label={t("openNavigation")} onClick={() => setMobileOpen(true)}><Menu className="h-5 w-5" aria-hidden="true" /></button>
            <div className="min-w-0"><p className="truncate text-sm font-semibold app-text">{t(pageKey)}</p><p className="truncate text-xs app-muted">{selectedWorkspace?.name ?? t("noWorkspace")} · {t("salesDepartment")}</p></div>
          </div>
          <div className="flex items-center gap-2">
            <label className="app-control flex items-center gap-1.5"><Languages className="hidden h-4 w-4 sm:block" aria-hidden="true" /><span className="sr-only">{t("language")}</span><select aria-label={t("language")} value={locale} onChange={(event) => setLocale(event.target.value as AppLocale)}><option value="en">EN</option><option value="fr">FR</option><option value="ar">العربية</option></select></label>
            <button type="button" className="app-icon-button" aria-label={t("switchTheme")} title={theme === "light" ? t("darkTheme") : t("lightTheme")} onClick={() => setTheme(theme === "light" ? "dark" : "light")}>{theme === "light" ? <Moon className="h-4 w-4" aria-hidden="true" /> : <Sun className="h-4 w-4" aria-hidden="true" />}</button>
            <div className="hidden items-center gap-2 border-s ps-3 md:flex">{user ? <Avatar name={user.display_name} email={user.email} /> : null}<div className="max-w-36"><p className="truncate text-xs font-semibold app-text">{user?.display_name || t("operator")}</p><p className="truncate text-[10px] app-muted" dir="ltr">{user?.email}</p></div></div>
            <button type="button" className="app-icon-button" onClick={logout} aria-label={t("logout")}><LogOut className="h-4 w-4" aria-hidden="true" /></button>
          </div>
        </header>
        <main className="min-h-[calc(100vh-4.25rem)]"><Outlet /></main>
      </div>
    </div>
  );
}
