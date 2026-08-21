# HIRI Open-Source Research

> **STATUS: ACTIVE RESEARCH REFERENCE**

Open-source repositories are resources for HIRI, not alternative product foundations.
HIRI architecture must not be restarted or replaced because a useful repository is discovered.

## Classification Rule

Every repository studied for HIRI must be classified as exactly one of:

A. Reuse code
B. Reuse architectural pattern
C. Integration
D. Ignore

Code classified as A must still receive a license review before commercial reuse.

## Current Research References

| Repository / Project | Classification | HIRI Use |
| --- | --- | --- |
| SalesGPT | B - Reuse architectural pattern | Sales-stage awareness and product/tool grounding |
| sales-outreach-automation-langgraph | B - Reuse architectural pattern | Research, qualification and personalized outreach graph ideas; no code copied because license was unclear during review |
| b2b-sdr-agent-template | B - Reuse architectural pattern | Business context, staged pipeline, memory, follow-up and delivery concepts |
| OpenCloser v2 | B - Reuse architectural pattern | Sales roles, CRM pipeline, coaching and manager-view concepts |
| Shubhamsaboo/awesome-llm-apps | B - Reuse architectural pattern | Reference source for multi-agent, MCP and routing patterns; individual components may later be reviewed separately for A or C classification |

## HIRI Reuse Rule

Before adopting anything from an external repository:

1. Check whether HIRI already has the concept.
2. Preserve the existing Workspace / Department / AIEmployee / Capability / Tool / WorkItem architecture.
3. Classify the repository as A, B, C or D.
4. Verify the exact license and commit before copying code.
5. Check tenant isolation, permissions, approvals, auditability and AI-cost implications.
6. Classify the resulting idea as NOW, NEXT or LATER.
7. Do not add infrastructure or complexity unless it solves a current HIRI requirement.

Before copying any third-party source file into HIRI, verify the license at the exact commit being used and preserve all required copyright and license notices.
