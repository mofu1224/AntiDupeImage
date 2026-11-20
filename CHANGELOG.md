# Changelog

All notable user-facing changes are documented here.
Format follows Keep a Changelog conventions; versioning follows SemVer.

## [Unreleased]

### Added
- クローン配布向けのWindows/macOSポータブル起動。公式ハッシュ付きローカルランタイム取得、リポジトリ内キャッシュ、静的UI配信、オフライン起動・移動後再構築、CI実機スモークテストを追加。
- 修正版の画像処理ライブラリを利用するため、手動開発で必要なPythonを3.11以降に変更。通常起動では固定版Pythonを自動取得します。
- `antidupeimage` command-line interface sharing the operation layer with the WebUI backend (one code path, identical validation and file-safety checks), with `--json` output, explicit `--confirm` gating for `move` and `reset`, loopback-only `serve`, and documented exit codes (0 success, 1 error, 2 not found, 3 scan wait timeout, 130 interrupted).
- HTTP routes refactored into thin wrappers over the shared operation layer (`app.services.operations`).
- Workflow stepper above the workspace tabs mapping setup / review / resolve steps to the existing panels (no tab-role changes; existing tests unaffected).
- `.env.example` documenting supported `ANTIDUPEIMAGE_*` overrides for manual development servers.
- `THIRD_PARTY_NOTICES.md` component/copyright listing and `CHANGELOG.md`.

### Changed
- データの既定保存先をリポジトリ内の `data/` へ変更。CLIP/CUDAは `--ml` / `--cuda` で追加する構成に変更。
- WebUI visual refresh (toned retro theme) with all class names, roles, wording, and layout variables preserved.

### Fixed
- ローカルデータベースが破損・使用不能な場合、またはデータフォルダへ書き込めない場合に、CLIが対処を案内するエラーメッセージで終了するように修正。
- Pythonの画像処理・非同期処理・Web境界・任意ML依存・ビルド依存を、公開アドバイザリに対応したロック版へ更新。
- ポータブル環境の二重使用を拒否し、セットアップ状態を原子的に保存。状態破損時も利用者データを保持して再構築。
- CLIの非有限・負の待機時間を処理開始前に拒否し、中断時のスキャンキャンセルとワーカー終了を通常のコマンド入口でも実施。
- バックエンドwheel/sdistへプロジェクト自身のMITライセンス原文を同梱。
- Windows launcher setup: npm.cmdによるバッチの制御移譲問題を修正。現行ポータブル版はローカルNode.jsからnpmのJSエントリを直接実行する。
- E2E duplicate-move selector made exact so the workflow-step buttons' embedded aria-labels cannot collide.
- Frontend dev dependency audit failures (vitest, baseline-browser-mapping, source-map-js) resolved via locked semver-compatible updates.

## [0.2.0] - 2026-09-28

### Added
- Exact decoded-pixel matching using RGBA pixels from every animation frame plus canonical playback timing and loop metadata.
- Visual similarity pipeline: per-channel perceptual hashes, dHash, wHash, RGB/HSV histograms, luma layout, two-scale structural similarity, edge layout, and low-information quality signals.
- Conservative multi-signal verifier before optional OpenCLIP confidence filtering.
- Precision modes: Exact pixel, Ultra strict, Conservative (default), Balanced, Broad, with threshold presets applied from the scan form.
- Performance profiles: Standard accuracy (default) and Lightweight fast (reduced-resolution evidence, no CLIP, no small-difference matching, capped candidates).
- Scan history with immutable scan-time image snapshots and reusable feature caches in SQLite (schema version 7).
- History reuse: load stored path, device, model, performance, precision, and threshold settings back into the scan form.
- Staged result loading: paginated group summaries with server-side filter/sort plus on-demand group detail with live file revalidation.
- Duplicate-folder moves with explicit confirmation, snapshot revalidation before and after moving, containment checks, rollback on integrity failure, and refusal to move every available group member.
- Local loopback API on `127.0.0.1` with API token, strict Host/origin checks, one-time bootstrap capability exchange, and request body limits.
- Native folder picker support on macOS and Windows.
- Windows (`AntiDupeImage.bat`) and macOS (`AntiDupeImage.command`) launchers with per-launch random ports, ownership checks, and `--setup-only` mode.
- Local precision evaluator (`backend/tools/evaluate_precision.py`) using a temporary database without copying source images into the repository.

### Changed
- Recommended keep is the highest-resolution image; groups contain only images directly matching the recommended keep image.
- Only exact-pixel matches are preselected; visual matches require explicit selection.
- Duplicate move folders (`AntiDupeImage_Duplicates_*`) are skipped during recursive scans unless selected directly.
- Legacy `/api/files/trash` endpoint retained only as a deprecated compatibility alias for duplicate-folder moves.

### Fixed
- Unauthenticated API requests are rejected before request-body parsing.
- Oversized request bodies return HTTP 413 instead of being processed.
- Image decoding is descriptor-bound, skips symlinks, and enforces frame/pixel/cancellation limits.
- Candidate-pair and comparison work budgets fail scans with actionable errors instead of unbounded memory growth.
- SQLite schema migrations are transactional; legacy v0.1 groups are cleared with a rescan warning.
- Thumbnail workload is bounded (incremental rendering, concurrency limit, LRU Blob cache).

### Security
- Persistent token stored with restricted permissions in the application data directory; never embedded in Vite-served JavaScript, launch arguments, browser history, or Web Storage.
- Short-lived bootstrap capability exchanged once from the expected per-launch UI origin, then deleted.
- See `SECURITY.md` for the local API and file-safety model.

## [0.1.0] - Prior schema

- Original v0.1 SQLite schema without scan-time snapshots.
- Upgrading clears legacy duplicate groups; existing scan rows receive a warning and should be rescanned.

0.2.0はソース内の版です。GitHub Release・対応タグは未作成です。
