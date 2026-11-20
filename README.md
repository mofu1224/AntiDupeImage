---
type: README
---

# AntiDupeImage

Local WebUI for visual duplicate image detection on Windows and macOS.

## What It Does

- Scans a local folder or one supported image path.
- Opens the native folder picker from the WebUI on macOS and Windows.
- Detects exact decoded-image matches and visually similar images.
- Uses RGBA pixels plus canonical playback timing and loop metadata from every animation frame for exact matching.
- Applies EXIF orientation before exact digesting, visual feature extraction, and thumbnail generation.
- Renders transparency over a deterministic checkerboard for visual comparison and thumbnails.
- Compares per-channel perceptual hashes, dHash, wHash, RGB/HSV histograms,
  luma layout, two-scale structural similarity, edge layout, and
  low-information quality signals.
- Verifies visual candidates with a conservative multi-signal check before
  optional OpenCLIP confidence filtering.
- Recommends the highest-resolution image to keep.
- Includes only images that directly match the recommended keep image in each group.
- Moves explicitly selected duplicate files into a newly created duplicate folder.
- Stores scan history, immutable scan-time image snapshots, and reusable feature caches in SQLite. A history row can load its stored path, device, model, performance, precision, and threshold settings back into the scan form for review before a new scan is started.

File names and paths are not used as duplicate signals. EXIF orientation is used
only to normalize displayed pixels.

## Safety Model

AntiDupeImage is local-first, but its backend can read and move files with the
permissions of the current user. The API therefore:

- Listens on `127.0.0.1` and requires a local API token.
- Rejects unauthenticated API requests before request-body parsing and limits authenticated request bodies.
- Rejects symbolic-link scan roots and skips symbolic links found inside scans.
- Stores a scan-time snapshot for every image.
- Revalidates file identity and decoded image content immediately before and after moving a file.
- Verifies that duplicate destinations remain inside the originally created duplicate folder and rolls back failed integrity checks where possible.
- Refuses to move every currently available image in a duplicate group.
- Requires an explicit confirmation flag for duplicate move requests.
- Automatically preselects only exact-pixel matches; visual matches require explicit selection.

The platform shortcut writes a short-lived, unpredictable bootstrap capability to
a permission-restricted local handoff file and gives the browser only that file's
non-secret path. The handoff redirects locally, the UI removes the fragment
immediately, and the expected per-launch UI may exchange it once with the
launcher-owned backend for the API token. The backend deletes the handoff on a
successful exchange; the launcher also deletes it on failure or exit. Neither the
persistent token nor bootstrap capability is placed in browser-launch arguments or
Vite-served JavaScript, and neither is stored in browser history or Web Storage.
Reloading the page or opening a new tab therefore requires a fresh launcher
bootstrap or manual token entry.
The persistent token is stored as `api-token` in the
AntiDupeImage application data directory with restricted permissions where
supported. A token entered manually in the UI is kept only in browser memory.

## クローンして起動する（ポータブル版）

ソースZIPを使う場合は、書き込み可能なフォルダへ展開し、`AntiDupeImage.bat` と `AntiDupeImage.command` があるフォルダを開いてください。Gitで取得する場合は以下のコマンドを使います。Windows 11 x64で初回構築と移動後の基本操作を確認済みです。Windows 10、macOS Intel、任意のML/GPU構成は実機未検証です。

Windows 10/11 x64、macOS Apple Silicon / Intel向けです。Python・Node.js・npm・uvの事前インストールや管理者権限は不要です。初回セットアップにはインターネット接続が必要です。Gitで取得する場合のみGitが必要で、ソースZIPの展開でも同じ構成を使えます。

```bash
git clone https://github.com/mofu1224/AntiDupeImage.git
cd AntiDupeImage
```

Windowsでは `AntiDupeImage.bat` をダブルクリックするか、次を実行します。

```bat
AntiDupeImage.bat
```

macOSでは次を実行します。

```bash
bash AntiDupeImage.command
```

初回に公式配布のuv・Python・Node.jsをリポジトリ内へ取得し、ロックファイルに従って依存関係を導入して画面をビルドします。uvとNode.jsのバージョン・公式SHA-256は `scripts/portable-runtime.json` に固定しています。Pythonはuvの検証付き管理ランタイムを利用します。OSへのインストール、永続的なPATH変更、レジストリ登録は行いません。

セットアップ後はビルド済み画面とAPIを同じ `127.0.0.1` の自動選択ポートで配信します。表示されたURLがその起動のURLです。終了はコンソールで `Ctrl+C`。旧 `setup_*` / `run_*` も同じ起動経路へ委譲します。

| 保存先 | 内容 |
|---|---|
| `.portable/tools/` | uv・Node.js・npm |
| `.portable/python/` | Pythonランタイム |
| `.portable/venv/` | 本番用Python依存 |
| `.portable/cache/` | uv・npm・モデル・一時ファイルのキャッシュ |
| `frontend/node_modules/`、`frontend/dist/` | 画面のビルド依存と生成物 |
| `data/` | SQLite・APIトークン |

これらの生成物・データはGit対象外です。詳細は [クローン配布・移動・更新手順](docs/portable_clone.md) を参照してください。

### オプション

```bat
AntiDupeImage.bat --setup-only
AntiDupeImage.bat --offline
AntiDupeImage.bat --ml --setup-only
AntiDupeImage.bat --cuda --setup-only
```

- `--setup-only`: 環境を構築・更新して終了。
- `--offline`: 保存済みランタイム・キャッシュだけで起動または再構築。
- `--ml`: 任意のPyTorch/OpenCLIPを追加。旧 `cpu` 引数も使用可能。
- `--cuda`: Windows向けCUDA 13.0の追加構成。対応GPU・ドライバーが必要。

macOSも同じオプションを使用できます（`--cuda` を除く）。基本の画像照合は追加MLなしで動き、通常セットアップで巨大なML依存を取得しません。選択したML構成は次回起動にも引き継ぎます。CLIPモデル重みの初回取得には別途ネット接続が必要です。

## Result Loading API

The review UI loads completed scan results in stages so the progress panel,
group list, selected group detail, failure list, and thumbnails can update
independently. Use `GET /api/scans/{scan_id}/group-summaries` for lightweight
paginated lists with server-side filter and sort. Use
`GET /api/scans/{scan_id}/groups/{group_id}` when the user selects a group; the
detail response revalidates live file status against the immutable
`scan_images` snapshot before showing move availability.

Scan history supports server-side status filtering with
`GET /api/scans?status=<status>`, where `status` is `queued`, `running`,
`completed`, `failed`, or `cancelled`. Omitting `status` preserves the existing
behavior and returns all scans; pagination totals apply after filtering.

## CLIP

CLIP matching is disabled by default because PyTorch and OpenCLIP are optional,
large dependencies. Install them with the launcher `--ml` or `--cuda` option. Enable **CLIP check** in the
scan form when they are available.

If CLIP cannot be loaded and **Fallback if CLIP fails** is enabled, the scan
continues using visual-hash matches and records a warning. Disable fallback when
a CLIP failure should fail the scan.

Device auto-selection order is CUDA, Apple Silicon MPS, then CPU. Explicitly
requested unavailable devices are rejected instead of silently falling back.

## Precision Modes

Visual matching uses a two-step pipeline: pHash narrows candidates, then a
multi-signal verifier rejects likely false positives. The verifier calculates a
two-scale SSIM-style structural score from the cached 32x32 luma layout, so
recompression, resizing, and light color changes can retain their spatial
evidence even when histogram bins shift. **Exact pixel** is the
strictest mode and groups only exact decoded-pixel matches; it does not use
visual similarity, small-difference matching, or CLIP filtering. **Ultra
strict** is the tightest non-exact mode and is intended for near-exact review:
non-exact matches must clear pHash distance 2, Histogram 0.99, layout 0.99,
edge 0.98, and all supporting hash checks. **Conservative** is the default and
uses pHash 10 and Histogram 0.90 as its visible defaults while prioritizing no
false positives over recall. **Balanced** relaxes the verifier
for resized or compressed near-duplicates. **Broad** is available when recall
matters more and manual review is expected.

Small local edits such as minor retouching, small overlays, or tiny erased
regions are checked with an additional high-evidence path. That path still
requires near-identical size, strong color similarity, layout similarity, edge
similarity, and supporting hash signals before a pair is grouped. In **Ultra
strict**, that path is tightened to the same near-exact 0.98/0.99-level evidence
instead of the normal small-difference limits. In **Exact pixel**, this path is
disabled entirely.

Selecting a precision mode in the scan form immediately applies matching pHash,
Histogram, and CLIP threshold presets. The numeric fields beside each slider can
then be adjusted directly, and those displayed values are submitted with the
scan request.

Exact decoded-pixel matches bypass the visual verifier. Low-information images
such as flat colors or sparse icons require much stricter evidence unless they
are exact-pixel matches. The compare view shows evidence values for visual
matches so each candidate can be reviewed before any duplicate move action.

The non-exact modes target the same image after recompression, resizing, light
tone changes, or a small local edit. Large crops, rotations, and merely similar
subjects or compositions are intentionally outside this matching contract.

## Performance Profiles

**Standard accuracy** is the default. It keeps the full visual evidence set,
small-difference matching, optional CLIP filtering, and current precision
behavior while avoiding repeated scan-time work where possible.

**Lightweight fast** prioritizes lower CPU and memory use for slower devices or
large folders. It keeps exact decoded-pixel matching and all scan snapshot/move
safety checks, but uses reduced-resolution visual evidence, disables CLIP,
skips small-difference matching, and caps visual candidates per image. This can
miss resized, compressed, or lightly edited duplicates. Lightweight features use
a separate cache revision so they do not replace standard-accuracy features.

## Local Precision Evaluation

Use the local evaluator with private images without copying them into the
repository. Manifest paths may be relative to `root`; every pair must be
labeled explicitly. Any predicted pair not listed in `positive_pairs` is
counted as a false positive.

```json
{
  "root": "/path/to/private-corpus",
  "positive_pairs": [["original.png", "recompressed.jpg"]],
  "negative_pairs": [["original.png", "different-image.png"]]
}
```

```bash
export UV_CACHE_DIR="$PWD/.uv-cache"
export UV_PROJECT_ENVIRONMENT="$PWD/.venv"
uv run --project backend --extra dev python backend/tools/evaluate_precision.py \
  --manifest /path/to/precision-manifest.json \
  --mode conservative
```

The evaluator uses a temporary SQLite database and prints aggregate metrics
only. It does not copy source images or persist their paths or results in the
repository.

## Command Line

The `antidupeimage` command runs the same shared operation layer as the
WebUI backend (`backend/app/services/operations.py`); the HTTP routes are
thin wrappers over those operations, so CLI invocations and WebUI actions
execute one code path with identical validation and file-safety checks.
The CLI works in-process against the local database and needs no API
token. Every command accepts `--json` for machine-readable output.

ポータブルセットアップ後は、追加ツールを導入せずにローカルPythonからCLIを使えます。

```powershell
.portable\venv\Scripts\python.exe -m app.cli --help
```

```bash
.portable/venv/bin/python -m app.cli --help
```

以下の `uv` の例は手動開発環境向けです。

```bash
export UV_CACHE_DIR="$PWD/.uv-cache"
export UV_PROJECT_ENVIRONMENT="$PWD/.venv"
export ANTIDUPEIMAGE_HOME="$PWD/.cli-data"  # optional: isolate CLI data
uv run --project backend antidupeimage system
uv run --project backend antidupeimage scan start /path/to/photos \
  --device cpu --precision exact_pixel --timeout 600
uv run --project backend antidupeimage scan list
uv run --project backend antidupeimage groups 1
uv run --project backend antidupeimage group 1 1
uv run --project backend antidupeimage failures 1
uv run --project backend antidupeimage thumbnail 3 --out /tmp/thumb.jpg
uv run --project backend antidupeimage move 1 1 3 --confirm
uv run --project backend antidupeimage scan delete 1
uv run --project backend antidupeimage reset --confirm
```

`scan start` waits for completion in the foreground; interrupting it or
reaching `--timeout` cancels the scan. `move` and `reset` require
`--confirm` and are refused without it. Exit codes are `0` on success,
`1` on validation or operation errors, `2` when the scan, group, or image
does not exist, and `3` when waiting for a scan times out.
待機時間は有限の0以上の秒数を指定します。中断は終了コード130でキャンセルし、ワーカーを終了します。

`antidupeimage serve --port 8000` starts the loopback WebUI backend
(refuses non-loopback bind addresses).

## Development

Install locked dependencies:

```bash
export UV_CACHE_DIR="$PWD/.uv-cache"
export UV_PROJECT_ENVIRONMENT="$PWD/.venv"
uv sync --project backend --locked --extra dev
npm ci --prefix frontend
```

Run the backend and frontend manually:

```bash
export ANTIDUPEIMAGE_API_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
uv run --project backend uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 --no-access-log
npm run dev --prefix frontend
```

Open `http://127.0.0.1:5173/#token=<URL-encoded token>` once, or paste the token
into the System panel. The frontend does not accept a build-time token variable;
this prevents production or development bundles from embedding API credentials.
API clients and thumbnail
requests use the `X-AntiDupeImage-Token` header. EventSource uses a `token`
query parameter because browsers do not support custom EventSource headers, so
avoid enabling access logs that record request URLs.

Feature extraction uses a small worker pool by default. Set
`ANTIDUPEIMAGE_FEATURE_WORKERS=1` for sequential extraction, or a higher value
when the machine can spare more CPU and disk I/O during scans. Lightweight fast
caps its feature extraction workers to keep load predictable on low-power
machines.

Resource safety limits can be adjusted for trusted workloads:

- `ANTIDUPEIMAGE_MAX_REQUEST_BODY_BYTES` (default `1048576`)
- `ANTIDUPEIMAGE_MAX_ANIMATION_FRAMES` (default `1000`)
- `ANTIDUPEIMAGE_MAX_DECODED_PIXELS` (default `50000000` cumulative pixels per image)
- `ANTIDUPEIMAGE_MAX_CANDIDATE_PAIRS` (default `100000`)
- `ANTIDUPEIMAGE_MAX_CANDIDATE_COMPARISONS` (default `1000000`)

Exceeding an image, candidate-pair, or comparison-work limit fails the affected
file or scan with an actionable error instead of silently reducing standard-mode
accuracy.

Checks:

```bash
export UV_CACHE_DIR="$PWD/.uv-cache"
export UV_PROJECT_ENVIRONMENT="$PWD/.venv"
uv run --project backend ruff check .
uv run --directory backend --project . pytest
uv lock --project backend --check
uv build --project backend
npm run test --prefix frontend
npm run build --prefix frontend
npm exec --prefix frontend -- playwright install chromium
npm run test:e2e --prefix frontend
npm audit --prefix frontend --audit-level=high
```

Playwright normally starts an isolated backend and Vite server on ports `8000`
and `5173`, then stops only those child processes and removes its temporary app
data. The normal E2E command refuses to run when either port is already in use.

Existing disposable E2E servers can be reused by setting
`ANTIDUPEIMAGE_E2E_REUSE_SERVERS=1` and setting
`ANTIDUPEIMAGE_E2E_TOKEN` to the token used to start the existing backend. Both
servers must already be ready; Playwright neither starts nor stops either one in
reuse mode. The E2E suite resets backend state before each test, so never point
reuse mode at a backend containing user data. Normal and reuse runs share the
fixed ports and must run sequentially.

Install `uv` on `PATH` before running the manual development commands. The
explicit project environment keeps backend commands in the same root `.venv`
used by manual development commands; the portable launcher uses `.portable/venv`.
The repo-local `.uv-cache` remains writable in
sandboxed or restricted developer environments.

## Supported Environments

- Windows 10/11 (x64) and macOS (Apple Silicon and Intel).
- ポータブル起動: Python 3.12.12 / Node.js 24.21.0 / uv 0.13.0を自動取得。
- 手動開発: Python 3.11〜3.14、Node.js 22.12以降、npm、uv。
- Linux is not packaged with a launcher, but the manual backend/frontend
  development commands use the same loopback-only configuration.
- CUDA execution is optional and Windows-only via the `ml-cu130` extra
  (CUDA 13.0 wheels). CPU-only operation is the default tested path.

## Basic Usage

1. Launch the app with `AntiDupeImage.bat` (Windows) or
   `./AntiDupeImage.command` (macOS).
2. Choose a folder (or one supported image path) in the scan form.
3. Select a precision mode and performance profile, then start the scan.
4. Review duplicate groups: each group shows a recommended keep image,
   match reason, similarity evidence, and live file availability.
5. Select files to move explicitly. Only exact-pixel matches are preselected.
6. Confirm the duplicate-folder move and verify the newly created
   `AntiDupeImage_Duplicates_*` folder under the scan root.

## Configuration

Copy `.env.example` to `.env` only when shell-based overrides are needed
for manual development servers. The launchers set per-launch ports and
handshake values automatically.
`.env` は自動では読み込みません。必要な値は起動するシェルの環境変数として明示設定してください。

Supported overrides include `ANTIDUPEIMAGE_HOME`, `ANTIDUPEIMAGE_DB`,
`ANTIDUPEIMAGE_API_TOKEN`, resource limits such as
`ANTIDUPEIMAGE_MAX_REQUEST_BODY_BYTES`, and optional CLIP defaults.
See `.env.example` for the full list and defaults.

## Data and Upgrades

SQLiteとAPIトークンは既定でリポジトリ内の `data/`、ポータブル起動のモデルキャッシュは `.portable/cache/` に保存します。`ANTIDUPEIMAGE_HOME` / `ANTIDUPEIMAGE_DB` を明示指定した場合は、その指定先を使用します。

従来版のユーザー領域データは自動移動しません。引き継ぐ場合は旧アプリを終了し、旧データフォルダの内容を `data/` へコピーするか、`ANTIDUPEIMAGE_HOME` で旧保存先を指定してください。更新前は `data/` をバックアップしてください。

The database uses schema migrations. When upgrading from the original v0.1
schema, legacy duplicate groups are cleared because they do not contain
scan-time snapshots. Existing scan rows receive a warning; rescan those paths to
restore results.

Deleting scan history does not delete image files. Duplicate file moves show
operation progress in the UI, create a new `AntiDupeImage_Duplicates_*` folder
under the scan root, re-check that the original path is no longer available,
and then remove resolved duplicate groups from the active result view.
The legacy `/api/files/trash` endpoint is kept only as a compatibility alias
for duplicate-folder moves; it does not send files to OS trash.
The System panel can reset the app back to an empty local database by clearing
scan history, scan-time snapshots, duplicate groups, failure records, and cached
image features. That reset does not delete source images, moved duplicate
folders, the API token, or external model caches, and it is rejected while a
scan is still queued or running.
Unreferenced cache rows are pruned when history is deleted. Feature caches are
automatically re-extracted when the feature algorithm revision changes.

## Limitations

- Symbolic links are intentionally not scanned.
- Animated-image visual matching uses the first displayed frame. Exact matching
  includes all decoded animation frames plus canonical playback timing and loop metadata.
- Duplicate move folders created by AntiDupeImage are skipped during recursive
  scans unless that folder is selected directly.
- Large similarity thresholds can reach the candidate-pair safety limit; use a
  smaller folder, stricter thresholds, or the lightweight profile.
- Transitive similarity chains can be split or omitted when an image does not
  directly match a group's recommended keep image.

## Update

Pull the latest `main`, then refresh locked dependencies without launching:

```bat
AntiDupeImage.bat --setup-only
```

```bash
./AntiDupeImage.command --setup-only
```

Database schema migrations run automatically. Rescan folders that show
an upgrade warning.

## Uninstall

1. アプリを終了し、必要な `data/` をバックアップします。
2. クローンしたフォルダを削除します。既定のランタイム・依存・キャッシュ・データも一緒に削除されます。
3. 保存先を明示変更した場合、そのデータは指定先に残ります。従来版のユーザー領域データも別途残ります。

スキャン元画像と `AntiDupeImage_Duplicates_*` はクローン外にあることがあります。内容を確認するまで削除しないでください。

## Troubleshooting

- `AntiDupeImage.bat --help` and `./AntiDupeImage.command --help` show
  launcher options, including CPU-only setup.
- If the UI shows no groups, check the failure list and scan warnings for
  unreadable files, permission errors, or candidate-budget limits.
- For candidate-budget failures, use a smaller folder, stricter thresholds,
  or the lightweight fast profile.
- If ports are already in use, stop the other process; launchers select
  fresh loopback ports per run and verify ownership before opening the UI.
- Do not include private images, tokens, personal paths, or database
  contents when reporting issues. See `SECURITY.md`.

## Collaboration

Use short-lived branches from `main` and open pull requests for review. Keep
local databases, model caches, scan results, virtual environments, `.env` files,
and generated frontend builds out of Git.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow.

## License

MIT. See [LICENSE](LICENSE). Third-party component licenses are listed in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Release history is in
[CHANGELOG.md](CHANGELOG.md).
