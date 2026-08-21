import { ArrowRight, CheckCircle2, LockKeyhole, ShieldCheck, UserCheck } from "lucide-react";

import { usePublicContent } from "../app/publicContent";
import { HiriSpatialCinematicScene } from "../components/public/HiriSpatialCinematicScene";
import { FinalCta, ProcessTrack, RouteLink, SectionHeading } from "../components/public/PublicSections";

export function LandingPage() {
  const copy = usePublicContent();
  const home = copy.home;

  return (
    <main id="main-content" className="public-site-page">
      {/* ════════════════════════════════════════════════════════════
          HERO — Dark Cinematic 3D Spatial Opening.
          Left ~38%: High-contrast editorial copy & CTAs.
          Right ~62%: HiriSpatialCinematicScene 3D floating stage.
          ════════════════════════════════════════════════════════════ */}
      <section className="public-home-hero hiri-hero-spatial-section">
        <div className="public-wrap hiri-hero-grid">

          {/* Left Column: High-contrast Editorial */}
          <div className="hiri-hero-editorial">
            <div className="hiri-hero-eyebrow-pill">
              <span className="hiri-eyebrow-dot" />
              AI WORKFORCE PLATFORM
            </div>

            <h1 className="hiri-hero-title">
              Hire your <span className="hiri-cyan-glow">AI</span> workforce.
            </h1>

            <p className="hiri-hero-lead">
              HIRI gives your business AI employees that perform real work across your tools and communication channels, while you control permissions, approvals and automation.
            </p>

            <div className="hiri-hero-cta-row">
              <a href="/register" className="hiri-btn-primary">
                {copy.common.create}
                <ArrowRight aria-hidden="true" width={16} height={16} />
              </a>
              <a href="/how-it-works" className="hiri-btn-secondary">
                {copy.common.how}
                <ArrowRight aria-hidden="true" width={16} height={16} />
              </a>
            </div>

            {/* Supporting Points */}
            <div className="hiri-hero-trust-grid" aria-hidden="true">
              <div className="hiri-trust-pill">
                <UserCheck width={15} height={15} className="hiri-trust-icon" />
                <span>Real business work</span>
              </div>
              <div className="hiri-trust-pill">
                <LockKeyhole width={15} height={15} className="hiri-trust-icon" />
                <span>Human in control</span>
              </div>
              <div className="hiri-trust-pill">
                <ShieldCheck width={15} height={15} className="hiri-trust-icon" />
                <span>Secure by design</span>
              </div>
              <div className="hiri-trust-pill">
                <CheckCircle2 width={15} height={15} className="hiri-trust-icon" />
                <span>Audit everything</span>
              </div>
            </div>
          </div>

          {/* Right Column: 3D Spatial Cinematic Stage */}
          <div className="hiri-hero-spatial-column">
            <HiriSpatialCinematicScene />
          </div>

        </div>
      </section>

      {/* ════════════════════════════════════════════════════════════
          REMAINING SECTIONS — Preserved for test & narrative continuity
          ════════════════════════════════════════════════════════════ */}
      <section className="public-section">
        <div className="public-wrap">
          <SectionHeading label={home.whatLabel} title={home.whatTitle} copy={home.whatCopy} />
          <div className="public-progression">
            {home.levels.map(([number, title, description]) => (
              <article key={number}>
                <span>{number}</span>
                <h3>{title}</h3>
                <p>{description}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="public-section public-section-tinted">
        <div className="public-wrap public-split">
          <SectionHeading label={home.flowLabel} title={home.flowTitle} copy={home.flowCopy} />
          <div>
            <ProcessTrack items={home.flow} numbered />
            <RouteLink to="/how-it-works">{copy.common.how}</RouteLink>
          </div>
        </div>
      </section>

      <section className="public-section">
        <div className="public-wrap">
          <div className="public-split public-split-top">
            <SectionHeading label={home.salesLabel} title={home.salesTitle} copy={home.salesCopy} />
            <div>
              <ProcessTrack items={home.salesFlow} />
              <RouteLink to="/sales">{copy.common.sales}</RouteLink>
            </div>
          </div>
          <div className="public-channel-line">
            <strong>{home.channelsLabel}</strong>
            {home.channels.map((channel) => (
              <span key={channel}>{channel}</span>
            ))}
          </div>
        </div>
      </section>

      <section className="public-section public-control-section">
        <div className="public-wrap public-split">
          <SectionHeading label={home.controlLabel} title={home.controlTitle} copy={home.controlCopy} />
          <div className="public-control-list">
            {home.controls.map(([title, description]) => (
              <div key={title}>
                <h3>{title}</h3>
                <p>{description}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="public-section">
        <div className="public-wrap">
          <div className="public-split public-split-top">
            <SectionHeading label={home.platformLabel} title={home.platformTitle} />
            <RouteLink to="/platform">{copy.common.platform}</RouteLink>
          </div>
          <div className="public-platform-rows">
            {home.areas.map(([title, description], index) => (
              <div key={title}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <strong>{title}</strong>
                <p>{description}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <FinalCta />
    </main>
  );
}
