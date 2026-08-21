## HIRI UI/UX Design Rules

All frontend, interface, dashboard, public-site, visualization, animation, and 3D work MUST follow:

`docs/design/HIRI_UI_UX_DESIGN_SYSTEM.md`

The design system is authoritative.

### HIRI Design Direction

HIRI combines:

**Professional Product Design + Experimental Digital Art Direction + Cinematic Product Storytelling**

HIRI must develop its own recognizable visual identity.

Do not default to generic AI/SaaS templates.

---

### Brand Experience vs Product Interface

HIRI has two different frontend modes that share one visual language.

#### Public HIRI / Brand Experience

Public pages may be:

* cinematic
* creative
* spatial
* strongly art-directed
* animated
* 2.5D / 3D
* editorial
* visually ambitious

The public experience should visually communicate HIRI as a living AI workforce.

Creative use of:

* lighting
* controlled gradients
* glow
* depth
* 3D
* isometric scenes
* scroll storytelling
* asymmetrical composition

is allowed when it has clear HIRI-specific meaning.

Do NOT automatically suppress these techniques merely because they are visually strong.

The rule is:

**Effects must belong to HIRI's visual world and communicate something.**

Examples:

* cyan/blue light = HIRI system activity
* illuminated path = WorkItem execution
* amber = human approval checkpoint
* green = successful business result
* spatial nodes = Workspace / Department / AIEmployee / Tool
* motion = system state change

---

#### Authenticated HIRI / Product Interface

The authenticated application must be considerably calmer.

Prioritize:

* usability
* information density
* fast workflows
* clear navigation
* professional tables
* lists
* inspectors
* forms
* approvals
* conversation interfaces
* operational visibility

Do not turn routine business software into a cinematic 3D interface.

Use the principle:

**Flat when reading.
Spatial when understanding.
Motion when something changes.**

---

### Before Implementing Any UI

1. Read `docs/design/HIRI_UI_UX_DESIGN_SYSTEM.md` completely.
2. Determine whether the task belongs to the Brand Experience or Product Interface.
3. Inspect existing HIRI components and adjacent screens.
4. Inspect the rendered UI where relevant.
5. Reuse existing components where appropriate.
6. Preserve current behavior.
7. Preserve EN / FR / AR localization and RTL.
8. Preserve accessibility.
9. Preserve responsive behavior.
10. Do not modify backend/domain/business logic as part of visual work unless explicitly required.

---

### Anti-Template Rule

Do not automatically generate:

* navbar + generic hero + three feature cards
* repeated feature-card grids
* fake client logos
* fake testimonials
* fake metrics
* generic glowing AI orb
* robot or AI-brain graphics
* generic purple AI gradient
* giant pill-shaped UI everywhere
* default shadcn-looking dashboards
* repetitive centered SaaS sections
* standard four-column footer without art direction
* decorative 3D objects unrelated to HIRI

Do not simply make an existing SaaS template darker and call it HIRI.

---

### HIRI-Specific Visual Language

Prefer visual concepts derived directly from the HIRI architecture:

Workspace / Tenant
→ Department
→ Department Supervisor
→ AIEmployee
→ Capability
→ Allowed Tool / Integration / MCP
→ Permissions / Policies
→ WorkItem
→ Approval when required
→ Business Result
→ Audit / Analytics

Visual storytelling should reinforce these concepts instead of inventing a different architecture.

---

### 3D and Motion

3D is allowed and encouraged for appropriate HIRI brand experiences.

Before introducing a new 3D or animation dependency:

1. Confirm existing tools cannot reasonably implement the requirement.
2. Check bundle impact.
3. Check performance.
4. Check accessibility.
5. Provide reduced-motion behavior.
6. Provide a lightweight fallback when appropriate.
7. Confirm the visual represents an actual HIRI concept.

Do not add 3D merely to make a screen look futuristic.

---

### Visual Review Required

Passing tests is not sufficient to approve significant design work.

For major frontend changes:

**implement
→ run
→ visually inspect
→ approve/revise
→ then expand**

Do not propagate an unapproved visual direction across the entire frontend.

---

### HIRI Identity Rule

When designing, do not ask:

> How can this look more futuristic?

Ask:

> How would HIRI visualize this?

The final result should feel like a digital world and operating interface that could only belong to HIRI.
