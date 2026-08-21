# HIRI UI/UX Design System

## 1. Purpose

This document is the authoritative visual, interaction, motion, and frontend design specification for HIRI.

All HIRI frontend work must follow this design system.

HIRI must NOT look like a generic AI-generated SaaS application.

HIRI should feel like a professionally designed, mature enterprise AI Workforce / AI Company Operating System.

The design should communicate:

* intelligence
* control
* trust
* precision
* automation
* reliability
* coordination
* sophistication
* operational visibility

Professional usability always comes before decoration.

---

# 2. Design Direction

HIRI's visual personality is:

**Precision + Sophistication + Trust + Controlled Futurism**

HIRI is not:

* a gaming interface
* a crypto dashboard
* a generic AI chatbot
* a Dribbble concept
* a marketing template
* a collection of shadcn cards
* a purple-gradient AI startup website

HIRI should look like software businesses can operate from every day.

---

# 3. Reference Products

Use mature products as references for design principles.

### Linear

Reference for:

* navigation discipline
* information density
* keyboard-first interactions
* subtle motion
* compact controls
* strong hierarchy

### Stripe Dashboard

Reference for:

* business information presentation
* data density
* forms
* settings
* financial-style dashboards
* tables
* filters
* professional workflows

### Notion

Reference for:

* workspace organization
* hierarchy
* content structure
* flexible navigation

### GitHub

Reference for:

* functional information density
* tables
* statuses
* technical workflows
* predictable interactions

These applications are references for **design philosophy only**.

Do not:

* copy branding
* copy logos
* copy proprietary illustrations
* reproduce screens pixel-for-pixel
* reproduce unique copyrighted assets
* make HIRI visually dependent on another brand

HIRI must develop its own recognizable identity.

---

# 4. Design Intelligence

When designing or modifying HIRI UI, follow principles from professional enterprise design systems and UX best practices.

Useful reference ecosystems include:

* UI/UX Pro Max design principles
* enterprise UI design principles
* shadcn/ui conventions
* WCAG accessibility guidelines

Before implementing a screen, determine:

1. What is the user's objective?
2. What is the primary action?
3. What information must be visible immediately?
4. What information can be secondary?
5. What existing HIRI components can be reused?
6. What states can this screen have?
7. How will the screen behave responsively?
8. What keyboard interactions are needed?
9. Does animation add functional value?
10. Does 3D add meaningful product value?

Do not begin by decorating the screen.

Begin with information architecture.

---

# 5. Anti-Generic-AI UI Rules

Never automatically generate the typical generic AI SaaS appearance.

Avoid:

* purple/blue AI gradients
* glowing borders everywhere
* neon effects everywhere
* glassmorphism everywhere
* excessive cards
* cards inside cards
* giant rounded containers
* excessive border radius
* pill-shaped controls everywhere
* huge dashboard headings
* enormous empty spaces
* random decorative blobs
* meaningless animated backgrounds
* fake metrics
* fake charts
* decorative statistics
* unnecessary icons
* robot illustrations
* AI brain graphics
* floating sparkles
* arbitrary shadows
* random colors
* excessive centered content
* repetitive three-card feature sections
* unnecessary hero sections inside the application
* excessive marketing language inside operational screens

Do not make a screen look "more modern" simply by adding visual effects.

First improve:

* hierarchy
* alignment
* typography
* spacing
* information architecture
* usability
* consistency

---

# 6. Layout System

Use a structured enterprise application layout.

Preferred patterns include:

* persistent application sidebar
* contextual page header
* compact toolbar
* breadcrumbs when hierarchy requires them
* structured content areas
* split views
* master-detail layouts
* tables
* lists
* drawers
* inspectors
* side panels
* command interfaces
* contextual actions

Do not put every piece of content inside a card.

Use sections, separators and surface hierarchy whenever they are more appropriate.

---

# 7. Spacing System

Use a **4px base spacing grid**.

Preferred spacing scale:

* 4px
* 8px
* 12px
* 16px
* 20px
* 24px
* 32px
* 40px
* 48px
* 64px

Avoid arbitrary spacing values unless technically necessary.

Maintain consistent vertical and horizontal rhythm across the application.

Operational screens should generally be more compact than marketing screens.

---

# 8. Border Radius

HIRI should use restrained corner rounding.

Recommended defaults:

* small controls: 4px
* inputs: 6px
* buttons: 6px
* panels: 6–8px
* dropdowns: 6–8px
* dialogs: 8–12px

Do not automatically use:

* 16px
* 20px
* 24px
* 30px

for ordinary UI containers.

Large radius values should exist only when there is a specific design reason.

---

# 9. Depth and Surfaces

Depth should primarily be communicated using:

* background surface hierarchy
* subtle borders
* contrast
* layering
* spacing

Use shadows only when they communicate actual elevation.

Avoid large decorative shadows.

Prefer a professional enterprise appearance over floating-card aesthetics.

---

# 10. Typography

Typography must prioritize readability and information hierarchy.

Use:

* compact page titles
* clear section titles
* readable body text
* restrained font weights
* predictable text sizes
* tabular numeric styles where useful
* monospace text for technical identifiers where appropriate

Avoid:

* oversized marketing typography inside the app
* unnecessary bold text
* excessive all-caps text
* too many font sizes

The application should feel dense but comfortable to read.

---

# 11. Colors

HIRI should have one deliberate brand system rather than random AI colors.

Use:

* neutral application surfaces
* one primary HIRI brand family
* semantic success colors
* semantic warning colors
* semantic error colors
* semantic information colors

Color must communicate meaning.

Do not use color simply to make a screen look interesting.

Status colors must remain consistent across all departments.

Example:

* success = successful/completed
* warning = requires attention
* danger = failure/destructive
* informational = neutral operational information

---

# 12. Buttons

Maintain a small predictable set of button styles.

Use:

* Primary
* Secondary
* Ghost
* Destructive
* Icon button

The strongest button should correspond to the primary action on the screen.

Avoid displaying several visually dominant actions simultaneously.

Do not make every button pill-shaped.

---

# 13. Inputs and Forms

Forms must resemble mature enterprise software.

Requirements:

* visible labels
* clear validation
* useful helper text when necessary
* consistent field heights
* predictable keyboard navigation
* clear required/optional distinction
* disabled states
* error states
* loading states
* success states

Avoid unnecessarily large form controls.

Do not hide important labels inside placeholders.

---

# 14. Tables

Use tables whenever users need to compare structured records.

HIRI will frequently need tables for:

* Leads
* AI Employees
* WorkItems
* Approvals
* Integrations
* Contacts
* Campaigns
* Departments
* Capabilities
* Audit events

Tables should support appropriate combinations of:

* sorting
* filtering
* search
* pagination
* column visibility
* row selection
* bulk actions
* contextual actions
* status indicators

Do not replace useful tables with decorative card grids.

---

# 15. Cards

Cards should only be used when the object genuinely behaves like an independent unit.

Good examples:

* AI Employee overview
* integration connection
* reusable template
* high-level metric
* plan/package selection

Do not use cards simply because they are easy to generate.

Avoid:

```text
Card
  └── Card
       └── Card
```

Prefer clear page structure.

---

# 16. Navigation

HIRI is a multi-department platform.

Navigation should communicate hierarchy clearly.

Conceptually:

```text
Workspace
   ↓
Departments
   ↓
AI Employees / Operations
   ↓
WorkItems / Leads / Campaigns / Tasks
```

The navigation system must remain usable as HIRI grows.

Avoid designing navigation that works only for the current Sales implementation.

HIRI should be capable of supporting multiple departments without major redesign.

---

# 17. Component Foundation

Use reusable primitives and HIRI-specific components.

shadcn/ui-compatible patterns may be used as the underlying component foundation.

However:

**HIRI must never look like an untouched shadcn template.**

The library is infrastructure.

It is not the HIRI brand.

Before creating a new component:

1. Search existing HIRI components.
2. Reuse an existing component when possible.
3. Check whether the component can be composed from existing primitives.
4. Use an appropriate shadcn/ui primitive when useful.
5. Build a custom HIRI component only when there is a genuine product need.

Avoid introducing several UI component libraries for the same purpose.

---

# 18. HIRI Component Identity

HIRI must eventually develop recognizable product components.

Examples may include:

* AI Employee identity block
* AI Employee status indicator
* Department status
* WorkItem status
* Capability badge
* Approval indicator
* Tool permission state
* Human takeover state
* AI execution state
* automation timeline
* workflow inspector

These should be consistent everywhere in HIRI.

---

# 19. Analytics

HIRI analytics should answer actual business questions.

Potential visualization areas include:

* department performance
* sales pipeline
* qualification performance
* response times
* WorkItem volume
* AI employee workload
* AI employee success rate
* approval frequency
* automation success/failure
* tool usage
* conversion performance
* marketing performance

Tremor-style dashboard patterns can be used where appropriate.

Charts should exist because they help users understand something.

Never create charts simply because data exists.

Prefer:

* meaningful KPI hierarchy
* period comparison
* trends
* funnels
* distribution
* status breakdown
* exceptions requiring attention

---

# 20. Motion System

HIRI may use modern animation.

Preferred approach:

**subtle, fast, purposeful motion**

Motion may be used for:

* panel transitions
* route transitions
* loading
* list insertion/removal
* expand/collapse
* workflow progression
* state changes
* success confirmation
* contextual overlays
* drawers
* command palettes
* notifications

Motion libraries such as Motion may be used.

Animations must never interfere with the user's work.

---

# 21. Motion Timing

Most application animations should remain short.

Typical range:

* micro-interaction: 100–180ms
* component transition: 150–250ms
* panel transition: 180–300ms

Longer animation should be reserved for intentional demonstrations or onboarding.

Avoid slow cinematic animations in everyday operations.

---

# 22. Reduced Motion

Respect:

```css
prefers-reduced-motion
```

Users must be able to use HIRI without unnecessary movement.

Important information must never depend exclusively on animation.

---

# 23. 3D Design Strategy

HIRI is allowed to use real-time 3D.

Potential technologies include:

* Three.js
* React Three Fiber
* Drei

However:

**3D is a strategic product visualization layer, not the default UI.**

HIRI's normal operational interface should remain fast, readable and mostly 2D.

---

# 24. Good Uses of 3D

## AI Workforce Visualization

HIRI may provide an optional spatial view of:

```text
Workspace
    ↓
Department
    ↓
Department Supervisor
    ↓
AI Employees
    ↓
Capabilities
    ↓
Tools / Integrations
    ↓
WorkItems
```

This can help users understand how their AI workforce is organized.

---

## Department Visualization

A spatial organization view could show:

```text
HIRI
 │
 ├── Sales
 │    ├── Supervisor
 │    ├── Lead Research AI
 │    ├── Qualification AI
 │    ├── Conversation AI
 │    └── Follow-up AI
 │
 ├── Marketing
 │    └── ...
 │
 └── Back Office
      └── ...
```

The visualization should remain optional.

---

## Workflow Visualization

HIRI may visually explain how work travels through the system:

```text
Request
   ↓
WorkItem
   ↓
Department Supervisor
   ↓
AI Employee
   ↓
Capability
   ↓
Tool / Integration
   ↓
Approval
   ↓
Action
   ↓
Result
```

A spatial visualization may help users understand complex automation.

---

## Landing Page

3D may be used more freely on HIRI's public marketing website.

Possible concept:

An abstract AI workforce network gradually comes online as departments and AI employees connect.

This should reinforce HIRI's identity.

It should not simply exist as decorative eye candy.

---

## Onboarding

Subtle spatial animation may illustrate:

* company creation
* department creation
* AI employee creation
* capability assignment
* tools being connected
* workforce activation

---

# 25. Bad Uses of 3D

Do NOT use 3D for:

* ordinary forms
* buttons
* data tables
* CRM lists
* settings pages
* approval lists
* normal navigation
* basic dialogs
* text-heavy interfaces
* routine operations

Do not convert basic UI elements into 3D objects simply because the technology is available.

---

# 26. 3D Performance Rules

Heavy 3D must be:

* lazy-loaded
* code-split
* performance monitored
* optional where appropriate

HIRI application startup must never depend on downloading large 3D assets.

Provide lightweight fallbacks for:

* low-power devices
* WebGL limitations
* reduced-motion preferences
* mobile devices where necessary

Avoid unnecessary:

* high-poly models
* oversized textures
* expensive lighting
* excessive particle systems
* constant GPU-heavy effects

Performance is more important than spectacle.

---

# 27. Icons

Use one consistent icon family whenever possible.

Icons must have functional value.

Use icons for:

* navigation
* actions
* statuses
* identifiable object types

Avoid decorative icon overload.

Do not randomly mix several icon styles.

---

# 28. Empty States

Empty states should explain:

1. what the section is
2. why it is empty
3. what the user can do next

Do not fill empty states with generic AI illustrations unless there is a strong reason.

Example:

Instead of:

> No AI Employees.

Use:

> No AI employees have been added to this department yet.

Then provide the appropriate action.

---

# 29. Loading States

Avoid large blocking spinners whenever possible.

Prefer:

* skeleton states
* local loading indicators
* optimistic updates where safe
* progressive loading

The interface should remain stable while data loads.

---

# 30. Error States

Error messages must:

* explain what failed
* avoid exposing sensitive internal information
* indicate whether retry is possible
* provide a recovery action when appropriate

Do not use generic:

> Something went wrong.

when HIRI knows the actual recoverable problem.

---

# 31. Status Design

HIRI contains many operational states.

Status design must remain consistent.

Examples include:

* created
* assigned
* running
* waiting
* approval required
* completed
* failed
* cancelled
* expired

Statuses should not rely only on color.

Use combinations of:

* text
* icon
* shape
* semantic color

where useful.

---

# 32. AI Employee Representation

AI employees should feel like operational software entities, not cartoon characters.

Avoid making every AI employee look like:

* a robot
* a human avatar
* a fantasy character

An AI Employee identity may communicate:

* name
* department
* role
* state
* capabilities
* autonomy level
* assigned work
* performance

HIRI may eventually develop a unique abstract visual language for AI workers.

---

# 33. AI Transparency

Users must understand when an action is:

* suggested by AI
* generated by AI
* automatically executed
* awaiting approval
* executed by a human
* handed over to a human

This distinction should be visually clear.

Do not hide automation behavior behind decorative UI.

---

# 34. Approval UX

Approvals are an important HIRI safety mechanism.

Approval interfaces should clearly communicate:

* what will happen
* which AI employee requested it
* which WorkItem triggered it
* which external action will occur
* affected customer/lead/entity
* relevant content
* approval consequences

Primary actions:

* Approve
* Reject

Additional actions may include:

* Modify
* Assign to human
* Inspect details

Avoid vague confirmation dialogs.

---

# 35. WorkItem UX

WorkItems are a core HIRI concept.

Their UI should make it easy to understand:

* what needs to happen
* who owns the work
* which department it belongs to
* which AI employee is responsible
* current status
* current step
* inputs
* outputs
* errors
* approvals
* related activity

WorkItem details may use a structured inspector or timeline.

---

# 36. Department UX

Each department should feel part of the same HIRI operating system.

Do not design:

* Sales as one visual product
* Marketing as another unrelated product
* Back Office as another unrelated product

Departments may have specialized workflows while sharing:

* navigation
* typography
* controls
* statuses
* page structure
* spacing
* interaction patterns
* HIRI identity

---

# 37. Responsive Design

HIRI is primarily a professional desktop application.

Desktop should receive the richest operational experience.

However, interfaces must remain usable on smaller screens.

Responsive behavior should prioritize:

1. essential information
2. primary actions
3. readable content
4. navigation accessibility

Large tables may use:

* horizontal scrolling
* responsive columns
* condensed views
* mobile-specific detail layouts

Do not simply shrink desktop screens.

---

# 38. Accessibility

All production UI should target modern accessibility practices.

Requirements include:

* keyboard navigation
* visible focus
* semantic HTML
* sufficient contrast
* accessible labels
* screen-reader-friendly controls
* reduced-motion support
* meaningful error messaging

Do not rely exclusively on:

* color
* hover
* animation
* icons

to communicate important information.

---

# 39. Dark Mode

If HIRI supports dark mode, it must be deliberately designed.

Do not simply invert colors.

Dark mode must maintain:

* hierarchy
* contrast
* readable text
* semantic colors
* subtle borders
* surface separation

Avoid excessive glowing/neon effects in dark mode.

---

# 40. Existing UI Modification Rule

When working on an existing HIRI screen:

**Never redesign it blindly.**

First inspect:

* current layout
* adjacent screens
* existing components
* current design system
* existing functionality
* application architecture

Preserve consistency unless there is a documented reason to change it.

---

# 41. Business Logic Protection

UI work must not accidentally change domain behavior.

Do not modify:

* tenant isolation
* permissions
* approvals
* WorkItem rules
* AI routing
* integrations
* business logic
* database behavior

simply because a screen is being redesigned.

Frontend and backend changes must remain intentionally separated.

---

# 42. No Unnecessary Dependencies

Do not add a dependency merely to implement one decorative effect.

Before introducing a frontend package:

1. Check existing dependencies.
2. Determine whether existing tools can solve the requirement.
3. Evaluate bundle impact.
4. Evaluate maintenance.
5. Evaluate accessibility.
6. Evaluate licensing.
7. Confirm that the benefit is meaningful.

HIRI should avoid frontend dependency bloat.

---

# 43. HIRI Visual Identity

HIRI must eventually become visually recognizable without its logo.

Develop a consistent:

* typography system
* spacing rhythm
* navigation style
* surface system
* color language
* icon treatment
* status language
* motion language
* AI Employee representation
* Department representation
* WorkItem representation
* workflow visualization language

The long-term goal is:

> Someone familiar with HIRI should recognize a HIRI screen even before seeing the HIRI logo.

---

# 44. Design Quality Checklist

Before declaring frontend work complete, verify:

* information hierarchy
* alignment
* spacing consistency
* typography consistency
* color consistency
* component reuse
* responsive behavior
* keyboard navigation
* accessibility
* focus states
* hover states
* loading states
* empty states
* error states
* disabled states
* success states
* motion behavior
* reduced-motion behavior
* performance
* mobile behavior
* design-system compliance
* no generic AI UI patterns
* no unnecessary visual decoration

---

# 45. Final Rule

When improving HIRI's UI:

Do not ask:

> What visual elements can we add?

Ask:

> What can we remove, organize, clarify or simplify so the user understands and controls the system faster?

HIRI must feel advanced because the **product is intelligent**, not because the interface is overloaded with futuristic decoration.

The final objective is:

**A distinctive, premium, trustworthy enterprise AI operating system that looks professionally designed rather than AI-generated.**
