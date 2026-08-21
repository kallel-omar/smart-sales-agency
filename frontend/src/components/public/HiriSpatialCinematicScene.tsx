import { ArrowRight, CheckCircle2, ShieldCheck, UserCheck, Lock, Activity, Cpu, Sparkles, Database, Layers, MessageSquare, ExternalLink } from "lucide-react";

export function HiriSpatialCinematicScene() {
  return (
    <div className="hiri-spatial-viewport" aria-label="Governed execution">
      <span className="sr-only">WI-SALES-014</span>

      {/* 3D Atmospheric Canvas Stage */}
      <div className="hiri-spatial-stage">

        {/* Ambient Volumetric Backlight & Light Orbs */}
        <div className="hiri-spatial-glow-cyan" aria-hidden="true" />
        <div className="hiri-spatial-glow-amber" aria-hidden="true" />
        <div className="hiri-spatial-glow-green" aria-hidden="true" />

        {/* Spatial 3D Perspective Plane Wrapper */}
        <div className="hiri-spatial-3d-scene">

          {/* Perspective Plane 1: Floating Integration Orbit Chips */}
          <div className="hiri-spatial-layer layer-back" aria-hidden="true">
            <div className="hiri-orbit-chip chip-whatsapp">
              <MessageSquare width={13} height={13} />
              <span>WhatsApp</span>
            </div>
            <div className="hiri-orbit-chip chip-hubspot">
              <Database width={13} height={13} />
              <span>HubSpot CRM</span>
            </div>
            <div className="hiri-orbit-chip chip-gmail">
              <Layers width={13} height={13} />
              <span>Gmail API</span>
            </div>
            <div className="hiri-orbit-chip chip-slack">
              <ExternalLink width={13} height={13} />
              <span>Slack Workspace</span>
            </div>
          </div>

          {/* Energy Beams Connecting Inbound to Core */}
          <svg className="hiri-spatial-energy-svg" aria-hidden="true">
            <defs>
              <linearGradient id="cyanBeam" x1="0%" y1="0%" x2="100%" y2="100%">
                <stop offset="0%" stopColor="#00c8ff" stopOpacity="0.8" />
                <stop offset="50%" stopColor="#2563ea" stopOpacity="0.4" />
                <stop offset="100%" stopColor="#00c8ff" stopOpacity="0.9" />
              </linearGradient>
              <filter id="glowBeam" x="-20%" y="-20%" width="140%" height="140%">
                <feGaussianBlur stdDeviation="3" result="blur" />
                <feComposite in="SourceGraphic" in2="blur" operator="over" />
              </filter>
            </defs>
            {/* Animated Laser Execution Path */}
            <path
              d="M 50,70 Q 180,110 320,150 T 560,220"
              stroke="url(#cyanBeam)"
              strokeWidth="2.5"
              fill="none"
              filter="url(#glowBeam)"
              className="hiri-laser-path"
            />
          </svg>

          {/* Perspective Plane 2: Main 3D Floating Core Platform */}
          <div className="hiri-spatial-layer layer-middle">

            {/* Central Workspace Tenant Node */}
            <div className="hiri-3d-node hiri-core-node">
              <div className="hiri-node-header">
                <span className="hiri-node-indicator indicator-cyan" />
                <span className="hiri-node-tag">CORE TENANT</span>
              </div>
              <div className="hiri-node-body">
                <strong className="hiri-node-title">Acme Corporation</strong>
                <span className="hiri-node-subtitle">Department: Sales & Revenue</span>
              </div>
              <div className="hiri-core-pulse-ring" />
            </div>

            {/* Floating WorkItem Execution Capsule */}
            <div className="hiri-3d-node hiri-workitem-node">
              <div className="hiri-node-header">
                <span className="hiri-node-tag tag-cyan">ACTIVE WORKITEM</span>
                <span className="hiri-live-pulse" />
              </div>
              <div className="hiri-node-body">
                <strong className="hiri-workitem-code">WI-SALES-014</strong>
                <p className="hiri-workitem-desc">Enterprise Lead Qualification & Enrichment</p>
              </div>
              <div className="hiri-node-footer">
                <span className="hiri-mini-tag">Priority: High</span>
                <span className="hiri-mini-tag">State: Running</span>
              </div>
            </div>

            {/* AI Employee Pod Node */}
            <div className="hiri-3d-node hiri-employee-node">
              <div className="hiri-node-header">
                <span className="hiri-node-tag tag-blue">AI EMPLOYEE</span>
                <Cpu width={14} height={14} className="hiri-icon-blue" />
              </div>
              <div className="hiri-node-body">
                <div className="hiri-employee-profile">
                  <div className="hiri-avatar-glow">SQ</div>
                  <div>
                    <strong>Qualification AI</strong>
                    <span className="hiri-supervisor-ref">Sales Supervisor</span>
                  </div>
                </div>
              </div>
              <div className="hiri-permission-bar">
                <span className="hiri-perm-badge perm-green"><CheckCircle2 width={10} height={10} /> CRM Allowed</span>
                <span className="hiri-perm-badge perm-amber"><Lock width={10} height={10} /> Email Gate</span>
              </div>
            </div>

          </div>

          {/* Perspective Plane 3: Foreground Suspended Floating Gates */}
          <div className="hiri-spatial-layer layer-front">

            {/* Amber Human Approval Checkpoint Gate */}
            <div className="hiri-3d-card hiri-amber-gate-card">
              <div className="hiri-gate-beacon" />
              <div className="hiri-gate-content">
                <div className="hiri-gate-header">
                  <span className="hiri-gate-badge">HUMAN APPROVAL GATE</span>
                  <UserCheck width={15} height={15} className="hiri-icon-amber" />
                </div>
                <strong className="hiri-gate-title">Consequential Action Checkpoint</strong>
                <p className="hiri-gate-sub">High-value deal proposal authorized by Sarah (Sales Ops)</p>
              </div>
            </div>

            {/* Green Successful Business Outcome Badge */}
            <div className="hiri-3d-card hiri-green-result-card">
              <div className="hiri-result-icon-bg">
                <CheckCircle2 width={18} height={18} />
              </div>
              <div className="hiri-result-content">
                <span className="hiri-result-label">BUSINESS RESULT DELIVERED</span>
                <strong className="hiri-result-title">Qualified Opportunity & Contract Drafted</strong>
                <span className="hiri-result-audit">Audit Log Encrypted & Saved</span>
              </div>
            </div>

          </div>

        </div>

        {/* Live System Activity Floating Monitor */}
        <div className="hiri-spatial-hud-bar">
          <div className="hiri-hud-item">
            <span className="hiri-hud-label">WORKFORCE STATUS</span>
            <strong className="hiri-hud-val hiri-cyan">WorkItems coordinated</strong>
          </div>
          <div className="hiri-hud-item">
            <span className="hiri-hud-label">GOVERNANCE LEVEL</span>
            <strong className="hiri-hud-val hiri-amber">Controlled automation</strong>
          </div>
          <div className="hiri-hud-item">
            <span className="hiri-hud-label">BUSINESS RESULTS</span>
            <strong className="hiri-hud-val hiri-green">Audited execution</strong>
          </div>
        </div>

      </div>
    </div>
  );
}
