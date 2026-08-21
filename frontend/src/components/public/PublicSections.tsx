import { ArrowRight, Check, ShieldCheck } from "lucide-react";
import { Link } from "react-router-dom";

import { useAppExperience } from "../../app/AppExperience";
import { usePublicContent } from "../../app/publicContent";

export function PageHero({ eyebrow, title, lead, children }: { eyebrow: string; title: string; lead: string; children?: React.ReactNode }) {
  return <section className="public-page-hero"><div className="public-wrap public-hero-grid"><div className="public-hero-copy"><p className="public-eyebrow">{eyebrow}</p><h1>{title}</h1><p className="public-hero-lead">{lead}</p>{children}</div></div></section>;
}

export function SectionHeading({ label, title, copy }: { label: string; title: string; copy?: string }) {
  return <div className="public-section-heading"><p className="public-eyebrow">{label}</p><h2>{title}</h2>{copy ? <p>{copy}</p> : null}</div>;
}

export function RouteLink({ to, children, primary = false }: { to: string; children: React.ReactNode; primary?: boolean }) {
  const { direction } = useAppExperience();
  return <Link className={primary ? "public-cta-primary" : "public-text-link"} to={to}>{children}<ArrowRight aria-hidden="true" className={direction === "rtl" ? "is-rtl" : undefined} /></Link>;
}

export function ProcessTrack({ items, numbered = false }: { items: readonly string[]; numbered?: boolean }) {
  return <ol className="public-process-track">{items.map((item, index) => <li key={item}><span>{numbered ? String(index + 1).padStart(2, "0") : <Check aria-hidden="true" />}</span><strong>{item}</strong></li>)}</ol>;
}

export function FinalCta() {
  const copy = usePublicContent();
  return <section className="public-final-cta"><div className="public-wrap"><div><p className="public-eyebrow">HIRI</p><h2>{copy.common.finalTitle}</h2><p>{copy.common.finalCopy}</p></div><div className="public-cta-row"><RouteLink to="/register" primary>{copy.common.create}</RouteLink><RouteLink to="/how-it-works">{copy.common.how}</RouteLink></div></div></section>;
}

export function GovernanceMark({ label = "HIRI" }: { label?: string }) {
  return <div className="public-governance-mark"><ShieldCheck aria-hidden="true" /><span>{label}</span></div>;
}
