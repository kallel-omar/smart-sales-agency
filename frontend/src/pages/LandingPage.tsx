import { ArrowDown, CheckCircle2, LockKeyhole, UserCheck } from "lucide-react";

import { usePublicContent } from "../app/publicContent";
import { FinalCta, ProcessTrack, RouteLink, SectionHeading } from "../components/public/PublicSections";

export function LandingPage() {
  const copy = usePublicContent();
  const home = copy.home;
  return <main id="main-content" className="public-site-page">
    <section className="public-home-hero">
      <div className="public-wrap public-home-hero-grid">
        <div className="public-hero-copy"><p className="public-eyebrow">{home.eyebrow}</p><h1>{home.title}</h1><p className="public-hero-lead">{home.lead}</p><div className="public-cta-row"><RouteLink to="/register" primary>{copy.common.create}</RouteLink><RouteLink to="/how-it-works">{copy.common.how}</RouteLink></div></div>
        <div className="public-execution-visual" aria-label={home.visualTitle}>
          <div className="execution-visual-header"><span>{home.visualTitle}</span><span className="execution-status"><i />{copy.common.controlled}</span></div>
          <div className="execution-request"><small>{copy.common.event}</small><strong>{home.request}</strong></div>
          <ArrowDown aria-hidden="true" />
          <div className="execution-workitem"><div><small>{copy.common.workItem}</small><strong>WI-SALES-014</strong></div><span>Sales</span></div>
          <div className="execution-columns"><div><UserCheck aria-hidden="true" /><small>{copy.common.employee}</small><strong>{home.assigned}</strong></div><div><LockKeyhole aria-hidden="true" /><small>{copy.common.tool}</small><strong>{home.permission}</strong></div></div>
          <ArrowDown aria-hidden="true" />
          <div className="execution-result"><CheckCircle2 aria-hidden="true" /><div><small>{copy.common.result}</small><strong>{home.output}</strong></div></div>
        </div>
      </div>
    </section>
    <section className="public-section"><div className="public-wrap"><SectionHeading label={home.whatLabel} title={home.whatTitle} copy={home.whatCopy} /><div className="public-progression">{home.levels.map(([number, title, description]) => <article key={number}><span>{number}</span><h3>{title}</h3><p>{description}</p></article>)}</div></div></section>
    <section className="public-section public-section-tinted"><div className="public-wrap public-split"><SectionHeading label={home.flowLabel} title={home.flowTitle} copy={home.flowCopy} /><div><ProcessTrack items={home.flow} numbered /><RouteLink to="/how-it-works">{copy.common.how}</RouteLink></div></div></section>
    <section className="public-section"><div className="public-wrap"><div className="public-split public-split-top"><SectionHeading label={home.salesLabel} title={home.salesTitle} copy={home.salesCopy} /><div><ProcessTrack items={home.salesFlow} /><RouteLink to="/sales">{copy.common.sales}</RouteLink></div></div><div className="public-channel-line"><strong>{home.channelsLabel}</strong>{home.channels.map((channel) => <span key={channel}>{channel}</span>)}</div></div></section>
    <section className="public-section public-control-section"><div className="public-wrap public-split"><SectionHeading label={home.controlLabel} title={home.controlTitle} copy={home.controlCopy} /><div className="public-control-list">{home.controls.map(([title, description]) => <div key={title}><h3>{title}</h3><p>{description}</p></div>)}</div></div></section>
    <section className="public-section"><div className="public-wrap"><div className="public-split public-split-top"><SectionHeading label={home.platformLabel} title={home.platformTitle} /><RouteLink to="/platform">{copy.common.platform}</RouteLink></div><div className="public-platform-rows">{home.areas.map(([title, description], index) => <div key={title}><span>{String(index + 1).padStart(2, "0")}</span><strong>{title}</strong><p>{description}</p></div>)}</div></div></section>
    <FinalCta />
  </main>;
}
