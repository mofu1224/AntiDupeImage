# Security Policy

AntiDupeImage is a local-first tool. It scans folders selected by the user and
stores its database and API token in repository-local `data/` by default.
Portable launchers keep OpenCLIP and Hugging Face model caches under
`.portable/cache/`. Explicit data-path overrides and manually started development
processes can use other locations; protect those locations as well.

## Local API

The backend is bound to `127.0.0.1`, validates the Host header, restricts browser
origins, and requires a local API token. CORS is not treated as authentication.

Do not expose the API port through a reverse proxy, port forward, public tunnel,
or container port binding. The portable launcher serves API and UI from one
random loopback port and verifies server ownership before opening the browser.
Platform launchers write a
separate short-lived bootstrap capability into a current-user-only local handoff
file. Only the non-secret file path is passed to the browser. The frontend removes
the redirected fragment immediately and may exchange it exactly once, from the
expected per-launch UI origin, for the persistent API token. The backend deletes
the validated handoff after exchange, while launcher cleanup removes it after
failure or shutdown. Neither bearer value is placed in browser-launch arguments or
Vite-served JavaScript, and neither is retained in browser history, localStorage,
or sessionStorage. A reload or new tab requires a fresh launcher bootstrap or
manual token entry. Protect the `api-token` file as a secret because the
API can read image paths, return thumbnails, and move verified duplicate files
into newly created folders.
It can also reset locally stored scan history and feature-cache rows; that reset
does not delete source image files or moved duplicate folders.
The legacy `/api/files/trash` endpoint is a deprecated compatibility alias for
duplicate-folder moves and does not send files to OS trash.
API clients and thumbnail requests should use the `X-AntiDupeImage-Token`
header. EventSource uses a `token` query parameter because browsers do not
support custom EventSource headers, so do not retain access logs that record
full request URLs.

## File Safety

- Symbolic-link roots are rejected and discovered symbolic links are skipped.
- Scan results use immutable scan-time image snapshots.
- Files are revalidated before thumbnail generation and duplicate move operations.
- The API refuses to move every currently available image in a group.
- The UI preselects only exact-pixel matches; visual matches require explicit selection.

These checks reduce accidental wrong-file move risk, but users should still
review each group and maintain backups of important files.

## Reporting a Vulnerability

Please open a private security advisory on GitHub if you find a vulnerability.

Include:

- Affected version or commit.
- Steps to reproduce.
- Expected and actual behavior.
- Any relevant logs, with personal file paths or private image details removed.

## Sensitive Data

Do not include private images, full local folder listings, API tokens, personal
paths, or database contents in public issues.
