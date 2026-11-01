# Agensi submission copy

Prepared 2026-11-01; not submitted. Free listing only. Field limits checked against [Submit on the website](https://www.agensi.io/docs/submit-on-the-website). A signed-in creator account is required; the available browser redirected to Sign In.

## Name

Paper Triage

## Summary

Assess one scientific paper against your research goal. Get its contribution, traceable evidence, limitations, relevance and a concrete next reading action. Free, open source, and grounded in the material your agent can actually read.

## Description

Paper Triage helps researchers decide whether and how to invest time in one paper.

Give your agent a PDF or readable full text, plus a short research goal. The skill guides a concise six-part report: reading basis, core idea, contribution, evidence and boundaries, relevance, and a MUST / WEEKLY / RADAR / DROP recommendation with one concrete next step.

Key claims should point to inspected figures, equations, tables or sections. The workflow separates author conclusions from interpretation, theory from experimental observations, and read material from missing supplements. Abstract-only access produces a provisional assessment.

Extracted from Jiaqi Xin's literature-agent research workflow. Free and open source under MIT. It requires no literature-agent installation, Obsidian vault, Zotero account or additional API key. The host agent's model costs and data handling still apply.

Two concise physics/materials examples and an explicit validation record are available in the documentation. They are curated authoring-run examples, not independent benchmarks. This skill supports reading decisions; it does not certify findings or independently establish novelty.

## See it in action

Paste the request and full result from [the theory example](examples/theory.md). Retain the version and reading-basis disclosure. Do not present it as a user testimonial or fresh-agent benchmark. The full result intentionally follows the example request's under-200-word limit.

## Compatibility note

Requires an agent that can load text instructions and read the supplied paper. Demonstrated by explicit file loading in a Codex authoring chat. PDF tools and network access depend on the host; automatic discovery, other hosts and Agensi-delivered execution have not been verified.

## Price

Free. Do not configure payouts, subscriptions or paid upgrades for this launch.

## Category and tags

Primary category suggestion: Education & Learning (choose the matching visible category label in the current form).

Tags: research, paper-reading, scientific-literature, evidence, physics, materials-science, reading-workflow.

Use cases: decide whether to read a paper; locate decisive evidence; plan a journal-club reading session.

## Limitations / FAQ

**Can it access subscription papers?** It uses only material the host can lawfully access or that you supply. It does not bypass access controls.

**Does it replace expert review?** No. It helps organize a first reading decision and makes uncertainty visible.

**Do I need Obsidian or Zotero?** No. The standalone skill delivers a report in the conversation by default.

**Does free mean no model costs?** The skill is free; your agent's subscription or usage costs still apply.

**Does it upload my paper to the creator?** It defines no creator endpoint or telemetry. Your host agent and connected services determine where your data is processed.

## Assets and links

- ZIP: build with `python3 scripts/package_paper_triage.py` from the repository root.
- Square icon: `assets/logo.png` (512 × 512).
- Optional cover: `assets/cover.png` (1280 × 640).
- Documentation: https://github.com/Xin-Jiaqi/literature-agent/tree/main/docs/paper-triage
- Source: https://github.com/Xin-Jiaqi/literature-agent/tree/main/skills/paper-triage

GitHub documentation and source links above are public on main. The versioned ZIP is also available at https://github.com/Xin-Jiaqi/literature-agent/releases/tag/paper-triage-v0.1.0 . If the platform name is taken, use “Literature Agent Paper Triage”; keep the local skill identifier unchanged.

## Account handoff

1. Sign in to https://www.agensi.io/dashboard/submit with the account you want associated with the public creator profile.
2. If needed, enable Creator Mode and complete the public profile. Any new terms acceptance remains with the user.
3. Upload the prepared ZIP and assets; paste the fields above; select Free.
4. Check the public preview, detected permissions and links, then submit for review. Approval and public availability are separate from submission.
