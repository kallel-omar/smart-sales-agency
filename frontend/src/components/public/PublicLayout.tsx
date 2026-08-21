import { Languages, Menu, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";

import { type AppLocale, useAppExperience } from "../../app/AppExperience";

const navigation = [
  ["platformNav", "/platform"],
  ["howItWorksNav", "/how-it-works"],
  ["salesNav", "/sales"],
  ["aboutNav", "/about"]
] as const;

export function PublicLayout() {
  const { direction, locale, t, theme } = useAppExperience();
  const [mobileOpen, setMobileOpen] = useState(false);
  const { pathname } = useLocation();

  useEffect(() => setMobileOpen(false), [pathname]);
  useEffect(() => {
    if (!mobileOpen) return;
    const close = (event: KeyboardEvent) => event.key === "Escape" && setMobileOpen(false);
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [mobileOpen]);

  return (
    <div className="hiri-public min-h-screen" data-theme={theme} dir={direction} lang={locale}>
      <a href="#main-content" className="public-skip-link">{t("skipContent")}</a>
      <header className="public-header">
        <div className="public-header-inner">
          <Link to="/" className="public-brand" aria-label="HIRI">
            <img src="/hiri-logo.svg" alt="" />
            <span>HIRI</span>
          </Link>
          <nav className="public-desktop-nav" aria-label={t("publicNavigation")}>
            {navigation.map(([key, path]) => (
              <NavLink key={path} to={path} className={({ isActive }) => isActive ? "is-active" : undefined}>{t(key)}</NavLink>
            ))}
          </nav>
          <div className="public-header-actions">
            <LanguageSelector />
            <Link className="public-login-link" to="/login">{t("loginAction")}</Link>
            <Link className="public-primary-action" to="/register">{t("createAccount")}</Link>
          </div>
          <button
            type="button"
            className="public-menu-button"
            aria-label={t(mobileOpen ? "closePublicNavigation" : "openPublicNavigation")}
            aria-expanded={mobileOpen}
            aria-controls="public-mobile-navigation"
            onClick={() => setMobileOpen((open) => !open)}
          >
            {mobileOpen ? <X aria-hidden="true" /> : <Menu aria-hidden="true" />}
          </button>
        </div>
        {mobileOpen ? (
          <nav id="public-mobile-navigation" className="public-mobile-nav" aria-label={t("mobileNavigation")}>
            {navigation.map(([key, path]) => <NavLink key={path} to={path}>{t(key)}</NavLink>)}
            <div className="public-mobile-controls"><LanguageSelector /><Link to="/login">{t("loginAction")}</Link><Link className="public-primary-action" to="/register">{t("createAccount")}</Link></div>
          </nav>
        ) : null}
      </header>
      <Outlet />
      <footer className="public-footer">
        <div className="public-footer-grid">
          <div><Link to="/" className="public-brand"><img src="/hiri-logo.svg" alt="" /><span>HIRI</span></Link><p className="public-footer-statement">{t("footerStatement")}</p><p>{t("footerDescription")}</p></div>
          <div><h2>{t("product")}</h2><Link to="/platform">{t("platformNav")}</Link><Link to="/sales">{t("salesNav")}</Link><Link to="/how-it-works">{t("howItWorksNav")}</Link></div>
          <div><h2>{t("companyLabel")}</h2><Link to="/about">{t("aboutNav")}</Link><Link to="/contact">{t("contactNav")}</Link><Link to="/login">{t("loginAction")}</Link></div>
        </div>
        <div className="public-footer-meta"><span>© 2026 HIRI</span><span>{t("footerControl")}</span></div>
      </footer>
    </div>
  );
}

function LanguageSelector() {
  const { locale, setLocale, t } = useAppExperience();
  return (
    <label className="public-language-control">
      <Languages aria-hidden="true" />
      <span className="sr-only">{t("language")}</span>
      <select aria-label={t("language")} value={locale} onChange={(event) => setLocale(event.target.value as AppLocale)}>
        <option value="en">EN</option><option value="fr">FR</option><option value="ar">العربية</option>
      </select>
    </label>
  );
}
