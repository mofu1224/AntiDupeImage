# AntiDupeImage repository guidance

## Scope

AntiDupeImage is a local-first duplicate-image review tool. The backend is Python/FastAPI under `backend/`; the frontend is React/TypeScript under `frontend/`.

Keep this file limited to durable, repository-specific guidance. Follow higher-priority user, platform, and global instructions first.

## Working rules

- Preserve unrelated staged, unstaged, and untracked work. Stage only files owned by the current task.
- Use `codex/*` branches and pull requests for normal changes to `main`.
- Do not add secrets, API tokens, private images, personal paths, folder listings, databases, model caches, or generated builds to Git.
- Keep the backend bound to loopback. Do not weaken Host, origin, bootstrap-capability, or API-token checks.
- Treat image paths and scan results as untrusted. Preserve symlink rejection, path containment, scan-time snapshots, pre-action revalidation, and rollback behavior.
- Never make a duplicate-file action capable of moving every available image in a group.
- Keep tests isolated from real application data and inherited `ANTIDUPEIMAGE_*` environment values.
- Before starting work, read `04_恒久方針/ユーザー指示の正本.md` in the shared Obsidian vault (`エージェント共通記憶`); it is the single always-checked note for the user's standing instructions, priorities, and confirmation boundaries. If the entry point is unclear, use `00_案内/00_案内インデックス.md` (vault hierarchy map).
- Then search the same vault per the global "共通Obsidian" rule for any instruction, policy, constraint, or past conclusion that could apply to this work, and apply what you find as a work condition. A full read or a full-vault sweep is not required.

## Validation

Backend:

```bash
uv sync --project backend --locked --extra dev
uv run --project backend ruff check .
uv run --directory backend --project . pytest
uv lock --project backend --check
uv build --project backend
```

Frontend:

```bash
npm ci --prefix frontend
npm run test --prefix frontend
npm run typecheck:e2e --prefix frontend
npm run build --prefix frontend
npm run test:e2e --prefix frontend
npm audit --prefix frontend --audit-level=high
```

Run the smallest relevant checks first, then the broader affected suite. Report checks that were not run or could not complete.

## Code Review Rules

### Local API boundary

- Flag any change that exposes the backend beyond loopback, accepts untrusted Host or origin values, places bearer values in browser history or persistent web storage, or permits protected API access before token validation. Safe path: preserve loopback binding, strict Host/origin checks, one-time bootstrap exchange, and token-first rejection.

### File safety

- Flag any change that follows symlinks, accepts paths outside the selected roots, acts on mutable live results without revalidation, permits moving every available group member, or records success before filesystem and database state agree. Safe path: reject or skip symlinks, enforce containment, use immutable scan snapshots, revalidate immediately before action, retain one source image, and roll back partial failures.

### Data and test isolation

- Flag schema or workflow changes that can partially commit migrations, leave scans in an incorrect terminal state, or let tests read or modify real user data. Safe path: use transactional migrations, explicit failure/cancellation states, repository- or OS-owned temporary directories, and cleared `ANTIDUPEIMAGE_*` variables.
