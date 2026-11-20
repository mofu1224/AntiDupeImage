# Contributing

Thanks for helping improve AntiDupeImage.

## Development Flow

1. Create a branch from `main`.
2. Keep each change focused on one bug, feature, or cleanup.
3. Add tests for user-visible behavior and file-safety changes.
4. Run the relevant checks before opening a pull request.
5. Describe user-visible behavior and database migration changes in the pull request body.

## Codex and GitHub Workflow

1. Start normal work from the latest `main` on a `codex/*` branch.
2. Open a pull request and describe the exact checks that completed.
3. Wait for `CI Gate` and resolve every review conversation.
4. Request `@codex review` when automatic Codex review is unavailable.
5. Use `@codex security review` for changes to authentication, path validation, file moves, or other trust boundaries.
6. Squash-merge the pull request and delete the temporary branch.

Wait for a successful `CI Gate` before merging. Never include API tokens, personal paths, private images, folder listings, or database contents in prompts, issues, pull requests, or logs.

## Local Setup

Windows:

```bat
AntiDupeImage.bat --setup-only
```

macOS:

```bash
chmod +x AntiDupeImage.command
./AntiDupeImage.command --setup-only
```

## Checks

Backend:

```bash
export UV_CACHE_DIR="$PWD/.uv-cache"
export UV_PROJECT_ENVIRONMENT="$PWD/.venv"
uv sync --project backend --locked --extra dev
uv run --project backend ruff check .
uv run --directory backend --project . pytest
uv lock --project backend --check
uv build --project backend
```

Install `uv` on `PATH`. Keep `UV_PROJECT_ENVIRONMENT` pointed at the root
`.venv` and `UV_CACHE_DIR` inside the repository for local sandboxed runs.

Frontend:

```bash
npm ci --prefix frontend
npm run test --prefix frontend
npm run typecheck:e2e --prefix frontend
npm run build --prefix frontend
npm exec --prefix frontend -- playwright install chromium
npm run test:e2e --prefix frontend
npm audit --prefix frontend --audit-level=high
```

GitHub Actions runs these checks for pushes to `main` and pull requests.

## Dependency Changes

- Update `backend/uv.lock` when changing `backend/pyproject.toml`.
- Update `frontend/package-lock.json` when changing `frontend/package.json`.
- Do not add optional ML packages to the core backend dependency set.
- Review dependency audit output before committing lock-file changes.

## Repository Hygiene

- Do not commit `.env` files, API tokens, virtual environments, local databases,
  model caches, scan output, or generated frontend builds.
- Do not commit sample images unless they are intentionally small,
  redistributable test fixtures.
- Use GitHub issues or pull request comments for design discussion before large
  rewrites.
