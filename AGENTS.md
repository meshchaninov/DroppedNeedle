# DroppedNeedle agent guide

This file is the working contract for AI-assisted changes in this repository. Read it
before editing code. User instructions and more specific `AGENTS.md` files take
precedence.

## Product and stack

DroppedNeedle is a self-hosted music request, discovery, playback, and native library
management application.

- Backend: Python 3.13, FastAPI, async services, SQLite in WAL mode.
- Frontend: SvelteKit, Svelte 5, strict TypeScript, Tailwind CSS, daisyUI.
- Package managers: pip through `backend/.venv`, and pnpm 10.
- Runtime: one Docker image. Local development uses two processes.

The backend's native engine follows this dependency direction:

```text
API routes -> services -> repositories -> infrastructure
```

Keep route handlers thin. Business rules belong in services, external I/O behind
repositories, and persistence or transport details in infrastructure. Do not bypass a
layer just to make a feature faster to implement.

## Start here

```bash
make doctor  # verify required tools
make setup   # create local env files and install dependencies
make dev     # backend on :8688 and frontend on Vite's default port
make check   # fast, non-mutating local quality gate
make ci      # full repository gate
```

Run `make help` to discover focused test targets. Prefer the narrowest relevant test
during implementation, then run `make check` before handing off a normal change.

## Working method

1. Read the issue/request, the relevant production code, and nearby tests before
   changing anything.
2. Inspect `git status`; preserve unrelated and user-authored changes.
3. State assumptions when requirements are ambiguous. Prefer a small reversible change
   over speculative scope.
4. Reuse established services, components, schemas, design tokens, and test patterns.
5. Add or update a regression test for behavior changes. Test observable behavior, not
   implementation details.
6. Run focused checks while iterating and the appropriate handoff checks at the end.
7. Summarize what changed, what was verified, and any remaining risk. Never claim a
   browser, Docker, integration, or real-client check that was not actually performed.

## Backend rules

- Use strong typing and async/await. Never perform blocking file or network I/O directly
  in an async request path.
- Validate input at the API boundary and keep response schemas stable.
- Keep secrets, credentials, tokens, filesystem internals, and raw exceptions out of
  responses and logs.
- Preserve authentication and ownership checks. Admin-only actions must remain explicit.
- Treat SQLite migrations, library scans, imports, file moves, tagging, and quarantine
  operations as high-risk. Make them recoverable, idempotent where practical, and cover
  failure paths with tests.
- Never use a real music library, download directory, or user database in tests. Use
  temporary directories and isolated databases.
- Add dependencies only when the standard library or an existing dependency is not a
  reasonable fit. Pin backend dependencies consistently with the existing files.

## Frontend rules

- Keep TypeScript strict; do not introduce `any` or suppress errors without a documented
  reason.
- Follow Svelte 5 patterns already used in neighboring components.
- Reuse TanStack Query keys and invalidation conventions. Keep user-scoped caches
  isolated across login/logout and user switching.
- Use named exports and async/await.
- Use semantic HTML, keyboard-accessible controls, visible focus, and useful labels.
- Use the existing theme tokens (`primary`, `secondary`, and related semantic tokens),
  not hard-coded colors.
- Every user action must visibly report success, failure, or in-progress state. Do not
  surface raw backend errors.

## Verification matrix

Choose checks based on the files and risk involved:

- Backend-only: focused pytest target, then `make backend-lint`.
- Frontend-only: focused Vitest project, then `make frontend-check`,
  `make frontend-lint`, and `make frontend-format-check`.
- Cross-layer/API contract: focused tests on both sides plus `make check`.
- Auth, secrets, downloads, library mutation, migrations, or startup: relevant focused
  suites plus `make security-tests`; use `make ci` when feasible.
- Docker/entrypoint/release behavior: build or exercise the relevant container path; do
  not treat unit tests as runtime proof.

Do not auto-format unrelated files. Do not commit, push, publish images, deploy, or
modify external services unless the user explicitly requests it.

## Definition of done

A change is done when its requested behavior is implemented, regression coverage exists
where appropriate, relevant checks pass, unrelated work is preserved, docs/examples are
updated when the public contract changes, and the handoff clearly distinguishes verified
facts from unverified assumptions.
