import { LogOut, Menu } from "lucide-react";
import { useState } from "react";
import { Outlet } from "react-router-dom";

import { useAuth } from "../../auth/AuthProvider";
import { Avatar } from "../ui/Avatar";
import { Button } from "../ui/Button";
import { Sidebar } from "./Sidebar";

export function AppShell() {
  const [mobileOpen, setMobileOpen] = useState(false);
  const { logout, user } = useAuth();

  return (
    <div className="min-h-screen bg-slate-50 text-slate-950">
      <div className="hidden lg:fixed lg:inset-y-0 lg:flex lg:w-72 lg:flex-col">
        <Sidebar />
      </div>

      {mobileOpen ? (
        <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal="true">
          <button
            className="absolute inset-0 bg-slate-950/60"
            aria-label="Close navigation"
            onClick={() => setMobileOpen(false)}
          />
          <div className="relative h-full w-80 max-w-[86vw] shadow-2xl">
            <Sidebar onNavigate={() => setMobileOpen(false)} />
          </div>
        </div>
      ) : null}

      <div className="lg:pl-72">
        <header className="sticky top-0 z-30 flex min-h-16 items-center justify-between border-b border-slate-200 bg-white/95 px-4 backdrop-blur sm:px-6">
          <div className="flex items-center gap-3">
            <Button
              type="button"
              variant="ghost"
              className="px-3 lg:hidden"
              aria-label="Open navigation"
              onClick={() => setMobileOpen(true)}
            >
              <Menu aria-hidden="true" className="h-5 w-5" />
            </Button>
            <div>
              <p className="text-sm font-semibold text-slate-950">Smart Sales Agency</p>
              <p className="text-xs text-slate-500">Sales department live first</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {user ? (
              <div className="hidden text-right sm:block">
                <p className="text-sm font-medium text-slate-950">{user.display_name || user.email}</p>
                <p className="text-xs text-slate-500">{user.email}</p>
              </div>
            ) : null}
            {user ? <Avatar name={user.display_name} email={user.email} /> : null}
            <Button type="button" variant="ghost" className="px-3" onClick={logout} aria-label="Log out">
              <LogOut aria-hidden="true" className="h-5 w-5" />
            </Button>
          </div>
        </header>
        <main className="min-h-[calc(100vh-4rem)]">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
