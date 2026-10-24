# Plan

**The plan of record is the GitHub [issues](https://github.com/nexuszephyr/AGENT-OPS/issues) and [milestones](https://github.com/nexuszephyr/AGENT-OPS/milestones)**, not this page. This page is a snapshot from the 24 Oct re-plan. If they disagree, the issues win.

Work on a branch (`om/*`, `abhinav/*`, `feat/*`, `docs/*`, …) and open every PR against `main`. `main` is protected: a PR needs the other person's approval and a green **CI passed** check.

## Sprints (24 Oct)

**Sprint 1: Core, features & demo readiness**

| Issue | Owner | |
|---|---|---|
| #10 Verify every scheme rule against official sources and set `last_verified` | Om | done (2026-10-24) |
| #11 Run the agent end to end with a real Claude API key | Om | done |
| #12 Harden prompts and reply parsing from the real-LLM run | Om | open |
| #36 ₹ value unlocked: rupee value per product and dashboard tiles | Abhinav | planned |
| #37 Branch-wide proactive scan: ranked call list of missed benefits (absorbs #14 extra personas) | Om | done (#43) |
| #39 Maker-checker approval: the preparer can't approve their own enrollment | Om | done (#41) |

**Sprint 2: Beautiful & interactive UI**

| Issue | Owner | |
|---|---|---|
| #16 UI design system: theme, typography, colours and layout shell | Abhinav | done |
| #17 Officer console redesign: customer picker, product cards, chat | Abhinav | open |
| #18 Live agent pipeline: Ingest → Decide → Act → Report with an activity timeline | Om | open |
| #19 Approval queue redesign: inline PDF preview, revision diff, one-click actions | Abhinav | open |
| #21 Branch dashboard redesign: KPI tiles, enrollment funnel, interactive charts | Om | open |
| #24 UI polish and accessibility pass | Abhinav | open |
| #26 UI tests: extend the smoke AppTest and publish screenshots from CI | Om | open |
| #38 Branch scan tab: call list UI with one-click "Start session" | Abhinav | planned |

**Sprint 3: Demo day**

| Issue | Owner | |
|---|---|---|
| #27 Record the backup demo video | Abhinav | open |
| #28 Update the pitch deck to BFSI framing with real screenshots | Om | open |
| #30 Full dress rehearsal: DEMO.md A1–A8 twice, including the rules-only fallback | both | open |

## Blocked / ready

Each issue lists its blockers. The **Issue dependencies** workflow labels it `status:blocked` while any blocker is open, and switches it to `status:ready` when the last blocker closes. Only pick up `status:ready` issues, and close issues from the PR (`Closes #N`) so their dependents unblock. Details: [CONTRIBUTING.md](../CONTRIBUTING.md#issues-and-sprints).

## Dropped in the re-plan (closed as not planned)

- **#13 Kannada text** for questions, explanations and customer messages. With an LLM key, the agent already writes customer-facing text in the customer's `preferred_language`; rules-only mode stays in English.
- **#15 Sarvam voice input.** A stretch goal that needed a new external service (Sarvam speech-to-text).
- **#22 Plan dependency graph.** The console's numbered plan already shows the order (seeding → eKYC → enrollments).
- **#23 Kannada / English toggle.** It depended on #13, which was dropped.

(#14 extra personas was folded into #37.)

## Cut order if behind

1. #24 UI polish → ship the design-system defaults
2. #26 CI screenshots → keep the existing smoke AppTest
3. #21 dashboard charts → plain numbers
4. #19 queue redesign → keep the current queue
5. Question ranking → fixed catalogue order

**Never cut:** rules engine, approval gate (including maker-checker), planner, audit log.

## Dependencies

Runtime: `requirements.txt` (Anthropic SDK by default; uncomment `openai` there for `LLM_PROVIDER=openai`). Dev and CI tools: `requirements-dev.txt` (ruff, mypy, pytest-cov, pytest-randomly, pip-audit).
