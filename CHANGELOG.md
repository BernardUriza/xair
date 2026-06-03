# Changelog

All notable changes to **xair** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Policy:

- **PATCH** (`0.0.x` → `0.0.x+1`): backwards-compatible bug fixes; no public-API change.
- **MINOR** (`0.x.y` → `0.x+1.0`): backwards-compatible feature additions (new contracts, new gatherer, new ack-meta options). May add `DeprecationWarning` on still-supported APIs.
- **MAJOR** (`x.y.z` → `x+1.0.0`): breaking changes to public API (signature of `dispatch`, `command`, `CommandContext` fields, contract Protocols, etc.). Migration notes mandatory.

Pre-1.0 (`0.x.y`): per project policy in [`.claude/rules/breaking-changes.md`](.claude/rules/breaking-changes.md), breaking changes are allowed in MINOR bumps; no deprecation shims required. Stability promise applies starting at 1.0.0.

---

## [Unreleased]

(Add entries here as work lands on `main`.)

---

## [0.0.1] — 2026-05-26

Initial public release of the engine-agnostic framework. Consumer-specific code (`pipelines/`, `stages/`, `tools/`, `config/`, frontend, and consumer-only providers) lives in downstream consumer packages, not here.

### Added

- Generic registry-driven dispatcher (`xair.dispatch.dispatch`) — knows ZERO specific commands; routes by `@command(name)` registration alone.
- `@command` decorator + `CommandContext` + `DispatchResult` in `xair.command_registry`.
- Contracts (`xair.contracts`): Protocols for `GitHubClient`, `IssueTrackerClient`, `LlmProvider`, `Publisher`, `Repository`, `Transport`, `ActionsIO`, `AgentRunner`, `Clock`, `FileStore`.
- Infra providers (`xair.infra`): `OpenAIProvider` (lazy-imported), `GitHubProvider`, `Slack`, `GraphQL`, `Container` with `_StubLlm` fallback so consumers without `OPENAI_API_KEY` still build the container.
- Gatherers (`xair.gatherers`): generic GitHub PR diff, commits, CI status, issues, threads, file context, rules.
- Base prompt builders (`xair.prompt`): `builder`, `claude_builder`, `agent_summary`, `trace_formatter`.
- Domain models (`xair.domain`): `Plan`, `Verdict`, `Narrator`, `DiffIndex`, `Invariants`, `AgentRun`.
- Orchestration (`xair.orchestration`): pipeline + stage executor + tracing.
- Multi-repo orchestrator (`xair.orchestrator`): umbrella-issue → DAG → multi-PR executor.
- Policies (`xair.policies`): budget, escalation, gating, routing, security.
- Testing fakes (`xair.testing.fakes`).
- Conda recipe: `noarch: python` — one artifact for linux/macOS/win.
- `publish.yml` tag-driven GitHub Actions workflow: push `v*` tag → builds sdist + conda package → publishes to anaconda.org.

### Documentation

- `CLAUDE.md` for Claude-Code-driven project sessions.
- `.claude/rules/framework-genericity.md` — never import consumer-specific code; lazy-load optional SDKs.
- `.claude/rules/registry-pattern.md` — commands self-register; dispatch routes via `_REGISTRY`.
- `.claude/rules/breaking-changes.md` — pre-1.0 policy: delete, do not deprecate.

### Known limitations

- Cross-repo `workflow_call` invocation has tripped `startup_failure` in production; consumers (e.g. BAIR Gatekeeper on free-intelligence) currently inline the workflow steps instead of using `uses: BernardUriza/.github/.github/workflows/*.yml@main`. Root cause not fully diagnosed.
- `xair --help` exits non-zero ("unknown command '--help'"). Dispatcher treats `--help` as a command name; fix planned for `0.0.2`.

[Unreleased]: https://github.com/BernardUriza/xair/compare/v0.0.1...HEAD
[0.0.1]: https://github.com/BernardUriza/xair/releases/tag/v0.0.1
