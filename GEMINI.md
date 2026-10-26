# Agent Workflow and Interaction Guidelines

These workflow guidelines govern all interactions in this project. You must strictly follow the three phases: **1. Understanding**, **2. Planning**, and **3. Implementation**. Do not skip phases or begin implementation autonomously without explicit approval.

---

## 1. Directory Structure Conventions

Maintain and utilize two dedicated directories:

1. `docs/`:
   - Overall design and architecture documents.
   - Shared decisions, constraints, and project context.
   - Incremental implementation plans with task-specific prompts.
   - Progress tracking, verification results, and remaining work.
   - *Source of truth for understanding progress across all sessions.*

2. `temp/`:
   - Reusable exploratory scripts, CLI commands, and temporary experiments.
   - Test inputs and generated artifacts for investigating or reproducing issues.
   - *Workspace to be reused across sessions; never regenerate scripts from scratch when an existing one can be reused or extended.*

---

## 2. Phase 1: Understanding — Explore Before Solving

When asked to understand, explore, or investigate a problem or feature:

- **Targeted exploration**: Explore only the relevant parts of the codebase:
  - The basic building blocks of the system.
  - How the relevant components interact.
  - The APIs, contracts, and interfaces involved.
  - The existing implementation and key design decisions.
- **Scope control**: Start with the high-level architecture and follow only the execution paths relevant to the task. Do not read the entire codebase or delve into unrelated implementation details.
- **Source of truth**: Use existing documentation and code as sources of truth. Distinguish verified facts from assumptions.
- **Interactive discussion**: Treat this phase as an interactive technical discussion. Explain concepts in simple terms, answer questions, and help build a mental model before proposing solutions.
- **Focus**: Keep responses focused on the question being discussed. Do not repeatedly summarize the entire system.
- **Constraints**:
  - Do **not** implement features or modify production code.
  - If a small exploratory script or command helps investigate, place it in or reuse from `temp/`.
  - If something important is unclear, investigate it together; do not jump to solutions prematurely.

---

## 3. Phase 2: Planning — Decide What to Build

When designing or planning changes:

- **Pre-planning review**: Read relevant project documentation in `docs/` (overall design, shared decisions, current plan/progress).
- **Interactive collaboration**:
  - Review the problem statement and proposed approach.
  - Identify gaps, potential issues, trade-offs, and simpler alternatives.
  - Help arrive at the best solution through conversation.
- **Constraints during planning**:
  - Do **not** generate implementation code.
  - Do **not** modify production code or configuration files.
  - Do **not** create or modify project files without explicit user approval.
  - Do **not** begin autonomous implementation.
  - Answer specific questions using established context; investigate additional files only when needed.
- **Plan creation**: Once agreed on the solution, produce an incremental implementation plan with steps that are as small and independent as reasonably possible.
  - For every step, provide:
    1. A simple description of what needs to be done and why.
    2. A self-contained prompt suitable for a fresh agent session.
    3. Relevant files, components, documentation, and dependencies.
    4. Clear acceptance criteria.
    5. Simple, human-verifiable steps with expected results for manual verification.
- **Approval gate**: Stop and ask if the implementation requires an unapproved design change, conflicts with existing behavior, or encounters unresolved decisions. **Do not begin implementation until the user explicitly approves the plan.**

---

## 4. Phase 3: Implementation — Execute, Understand, Verify

When executing an approved implementation step:

- **Pre-implementation check**: Read the overall design, shared decisions, current plan/progress, and the specific task prompt with its verification criteria.
- **Scope**: Implement **only** the single approved step, adhering strictly to agreed acceptance criteria.
- **Code quality guidelines**:
  - Write the simplest code that correctly solves the problem.
  - Use meaningful, human-readable variable, function, and class names.
  - Follow appropriate design principles (including SOLID where applicable) without unnecessary complexity or premature abstractions.
  - Reuse existing abstractions, scripts, and conventions.
  - Avoid hardcoding values that should be configurable.
  - Add appropriate error handling and useful logs without unnecessary noise.
  - Keep comments concise and explain intent rather than restating code.
  - Avoid unrelated refactoring or changes outside the approved scope.
- **Exploration & temporary artifacts**:
  - Reuse existing scripts from `temp/`.
  - Keep exploratory scripts and temporary artifacts separate from production code.
  - Preserve useful artifacts until the task is verified.
- **Verification protocol**:
  1. Run relevant automated tests and checks.
  2. Follow and document every human-verification step specified in the plan.
  3. Explain what the user should do, what to observe, and what the expected result means.
  4. Explicitly report any check that could not be run; never claim success without evidence.
  5. Do **not** skip human verification simply because automated tests pass.
- **Code review & explanation**:
  - Explain changes at the function and class levels.
  - Describe how modified code integrates into the existing system.
  - Highlight key design decisions, edge cases, and potential failure modes.
- **Documentation & progress tracking**:
  - Update the implementation plan in `docs/` with actual progress and verification results.
  - Update shared decisions and architecture documents if needed.
  - Record remaining work and unresolved issues.
  - Identify temporary files to be cleaned up before committing.
  - Never delete or overwrite unrelated user files.
- **Stop conditions**:
  - If implementation requires an unapproved design change, conflicts with the plan, or encounters ambiguity affecting correctness: **stop immediately, explain the issue, and ask for a decision**.
- **Completion summary**:
  - Summarize what changed and why.
  - Report pass/fail status for tests and human-verification steps.
  - Note what remains incomplete and follow-up actions required.
  - **Do not proceed to the next implementation step without user review and approval.**

