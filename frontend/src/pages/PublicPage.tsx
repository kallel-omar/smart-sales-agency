import { CheckCircle2, LockKeyhole, Network, ShieldCheck, UserRoundCheck } from "lucide-react";

import { usePublicContent } from "../app/publicContent";
import { FinalCta, GovernanceMark, PageHero, ProcessTrack, RouteLink, SectionHeading } from "../components/public/PublicSections";

type PublicPageName = "platform" | "how" | "sales" | "about" | "contact";

export function PublicPage({ page }: { page: PublicPageName }) {
  if (page === "platform") return <PlatformPage />;
  if (page === "how") return <HowPage />;
  if (page === "sales") return <SalesPage />;
  if (page === "about") return <AboutPage />;
  return <ContactPage />;
}

function PlatformPage() {
  const copy = usePublicContent(); const page = copy.platform;
  return <main id="main-content" className="public-site-page">
    <PageHero eyebrow={page.eyebrow} title={page.title} lead={page.lead}><div className="public-hero-architecture"><span>Workspace</span><i>→</i><span>Department</span><i>→</i><span>AIEmployee</span><i>→</i><span>WorkItem</span><i>→</i><span>{copy.common.result}</span></div></PageHero>
    <section className="public-section"><div className="public-wrap public-split"><SectionHeading label={page.workforceLabel} title={page.workforceTitle} copy={page.workforceCopy} /><div className="public-specification"><div className="public-spec-header"><UserRoundCheck aria-hidden="true" /><div><small>Department · Sales</small><strong>AIEmployee</strong></div><span>HIRI</span></div>{page.employeeFields.map(([name, description]) => <div key={name}><strong>{name}</strong><span>{description}</span></div>)}</div></div></section>
    <section className="public-section public-section-tinted"><div className="public-wrap"><SectionHeading label={page.workLabel} title={page.workTitle} copy={page.workCopy} /><div className="public-lifecycle"><ProcessTrack items={page.lifecycle} numbered /><p>{page.exceptions}</p></div></div></section>
    <section className="public-section"><div className="public-wrap public-split"><div><SectionHeading label={page.govLabel} title={page.govTitle} /><div className="public-guardrails">{page.guardrails.map(item => <span key={item}><ShieldCheck aria-hidden="true" />{item}</span>)}</div></div><div className="public-autonomy-ladder">{page.autonomy.map(([level, title, description]) => <div key={level}><span>{level}</span><div><strong>{title}</strong><p>{description}</p></div></div>)}</div></div></section>
    <section className="public-section public-control-section"><div className="public-wrap"><SectionHeading label={page.connectLabel} title={page.connectTitle} copy={page.connectCopy} /><div className="public-connection-table"><div className="connection-core"><Network aria-hidden="true" /><strong>HIRI</strong><span>Decisions · state · audit</span></div><div className="connection-list">{page.connections.map(item => <span key={item}>{item}</span>)}</div></div></div></section>
    <section className="public-section"><div className="public-wrap public-split"><SectionHeading label={page.gatewayLabel} title={page.gatewayTitle} copy={page.gatewayCopy} /><ul className="public-checked-list">{page.gatewayItems.map(item => <li key={item}><CheckCircle2 aria-hidden="true" />{item}</li>)}</ul></div></section>
    <section className="public-section public-section-tinted"><div className="public-wrap public-split"><SectionHeading label={page.contextLabel} title={page.contextTitle} copy={page.contextCopy} /><div className="public-object-list">{page.objects.map(item => <span key={item}>{item}</span>)}</div></div></section><FinalCta />
  </main>;
}

function HowPage() {
  const copy = usePublicContent(); const page = copy.how;
  return <main id="main-content" className="public-site-page">
    <PageHero eyebrow={page.eyebrow} title={page.title} lead={page.lead}><div className="public-hero-architecture"><span>{copy.common.event}</span><i>→</i><span>{copy.common.workItem}</span><i>→</i><span>{copy.common.employee}</span><i>→</i><span>{copy.common.approval}</span><i>→</i><span>{copy.common.result}</span></div></PageHero>
    <section className="public-section"><div className="public-wrap"><SectionHeading label={page.processLabel} title={page.processTitle} /><ol className="public-detailed-process">{page.steps.map(([number, title, description]) => <li key={number}><span>{number}</span><div><h3>{title}</h3><p>{description}</p></div></li>)}</ol></div></section>
    <section className="public-section public-control-section"><div className="public-wrap"><SectionHeading label={page.differenceLabel} title={page.differenceTitle} /><div className="public-comparison"><article><span className="comparison-state">×</span><h3>{page.unrestricted}</h3><p>{page.unrestrictedCopy}</p></article><article className="is-hiri"><GovernanceMark label={page.hiriModel} /><div className="public-formula">{page.formula.map(item => <span key={item}>{item}</span>)}</div></article></div></div></section>
    <section className="public-section"><div className="public-wrap public-boundary"><LockKeyhole aria-hidden="true" /><div><h2>{page.boundaryTitle}</h2><p>{page.boundaryCopy}</p></div></div></section><FinalCta />
  </main>;
}

function SalesPage() {
  const copy = usePublicContent(); const page = copy.sales;
  return <main id="main-content" className="public-site-page">
    <PageHero eyebrow={page.eyebrow} title={page.title} lead={page.lead}><ProcessTrack items={page.flow} /></PageHero>
    <section className="public-section"><div className="public-wrap public-split"><SectionHeading label={page.captureLabel} title={page.captureTitle} copy={page.captureCopy} /><div className="public-source-list">{page.sources.map(source => <span key={source}>{source}</span>)}</div></div><div className="public-wrap public-intent-flow"><ProcessTrack items={page.intentFlow} numbered /></div></section>
    <section className="public-section public-section-tinted"><div className="public-wrap public-editorial-rows">{page.stages.map(([title, description], index) => <article key={title}><span>{String(index + 1).padStart(2, "0")}</span><h2>{title}</h2><p>{description}</p></article>)}</div></section>
    <section className="public-section public-control-section"><div className="public-wrap public-split"><SectionHeading label={page.controlLabel} title={page.controlTitle} /><div className="public-control-list compact">{page.controls.map(([title, description]) => <div key={title}><h3>{title}</h3><p>{description}</p></div>)}</div></div></section>
    <section className="public-section"><div className="public-wrap"><SectionHeading label={page.viewLabel} title={page.viewTitle} copy={page.viewCopy} /><div className="public-operating-view">{page.operating.map(([title, description]) => <div key={title}><strong>{title}</strong><span>{description}</span></div>)}</div><RouteLink to="/register" primary>{copy.common.create}</RouteLink></div></section><FinalCta />
  </main>;
}

function AboutPage() {
  const copy = usePublicContent(); const page = copy.about;
  return <main id="main-content" className="public-site-page public-about-page">
    <PageHero eyebrow={page.eyebrow} title={page.title} lead={page.lead} />
    <section className="public-section"><div className="public-wrap public-split"><SectionHeading label={page.missionLabel} title={page.missionTitle} copy={page.missionCopy} /><div className="public-principles">{page.principles.map(([title, description], index) => <div key={title}><span>{String(index + 1).padStart(2, "0")}</span><div><h3>{title}</h3><p>{description}</p></div></div>)}</div></div></section><FinalCta />
  </main>;
}

function ContactPage() {
  const copy = usePublicContent(); const page = copy.contact;
  return <main id="main-content" className="public-site-page public-contact-page">
    <PageHero eyebrow={page.eyebrow} title={page.title} lead={page.lead} />
    <section className="public-section"><div className="public-wrap public-contact-options"><article><span>01</span><h2>{page.accessTitle}</h2><p>{page.accessCopy}</p><RouteLink to="/register" primary>{copy.common.create}</RouteLink></article><article><span>02</span><h2>{page.returnTitle}</h2><p>{page.returnCopy}</p><RouteLink to="/login">{page.login}</RouteLink></article><article><span>03</span><h2>{page.directTitle}</h2><p>{page.directCopy}</p></article></div></section>
  </main>;
}
