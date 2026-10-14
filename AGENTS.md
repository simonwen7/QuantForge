# QuantForge — Agent Development Instructions

## 1. Project Mission

QuantForge is a reproducible quantitative research and event-driven backtesting platform.

Primary objectives:
- Financial data engineering and validation
- Statistical research and trading signal development
- Correct event-driven backtesting
- Portfolio accounting and risk analytics
- Out-of-sample evaluation
- Reproducible experiments and automated testing

This is a research and simulation platform, NOT a live trading system.

## 2. Strict Git Restrictions

**The human user exclusively controls all Git write operations.**

NEVER execute:
- `git init`, `git add`, `git commit`, `git push`
- `git merge`, `git rebase`, `git reset`
- `git checkout`, `git switch`, `git stash`
- `git pull`, `git fetch`, `git clean`
- Git tagging, branching, releases, or remote modifications
- Any equivalent Git operation through scripts, APIs, IDE integrations, or other tools

Read-only Git inspection, such as `git status` and `git diff`, is permitted.

Never automatically commit or push, even after successful tests.

## 3. Milestone Workflow

The project follows a fixed 10-milestone development roadmap.

Every milestone has three phases:

1. Read Code / Review
2. Cursor Implementation
3. Test / Verification

Do not advance to another milestone without explicit user approval.

Within a milestone, related small implementation tasks may be grouped when authorized.

## 4. Implementation Rules

- Use Python 3.12 and uv for the initial environment.
- Follow the existing architecture and repository conventions.
- Read relevant code before making changes.
- Implement only the explicitly requested scope.
- Do not silently introduce new dependencies.
- Do not delete or overwrite unrelated files.
- Do not perform large refactors without permission.
- Prefer simple, testable, modular solutions.
- Use English for code, identifiers, documentation, and comments.

## 5. Quantitative Research Standards

Initial research market: BTCUSDT Spot, 1-hour OHLCV.

Tentative data source: Binance Vision, subject to licensing review.

Required principles:
- Prevent look-ahead bias and data leakage.
- Validate timestamps, schemas, and market data.
- Document transaction fees and slippage assumptions.
- Maintain correct cash and position accounting.
- Use deterministic and reproducible experiments.
- Separate development, validation, and final out-of-sample evaluation.
- Report negative results and research limitations.
- Never fabricate performance metrics, test results, or benchmarks.

## 6. Testing Requirements

- Add meaningful unit and integration tests.
- Test financial correctness, not merely code execution.
- Verify edge cases and invariants.
- Never claim tests passed without actual evidence.
- Never remove or weaken tests merely to obtain a passing result.
- Report skipped, failed, or unexecuted tests explicitly.

## 7. Transparency and Communication

Immediately report:
- Bugs and failed tests
- Architecture concerns
- Security and data licensing risks
- Unverified assumptions
- Statistical or financial correctness concerns
- Unexpected changes to project files
- Missing requirements and unresolved decisions

Never hide problems, silently change requirements, or describe unfinished work as complete.

Challenge questionable technical decisions and explain alternatives.

## 8. Required Completion Report

After each implementation task, provide:

1. Files created or modified
2. Summary of implementation
3. Tests executed and actual results
4. Tests not executed
5. Known issues and risks
6. Decisions requiring user approval
7. Recommended next verification step

Do not begin unrelated follow-up work automatically.

## 9. User Control

The assistant and Cursor Agent are implementation collaborators.

The human user retains final control over:
- Architectural approvals
- Milestone acceptance
- Dependency decisions
- Git history and repository operations
- External publishing and deployment

When uncertain, stop and request clarification rather than assuming authorization.
