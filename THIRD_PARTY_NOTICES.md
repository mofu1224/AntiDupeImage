# Third-Party Notices

This project is distributed under the MIT License (see `LICENSE`).
This file lists third-party components used to build and run it.

This is a preliminary inventory, not a completed redistribution clearance.
Exact versions, original license texts, NOTICE files and native components
must be checked against the final archive. MPL-2.0 applies to the runtime
package certifi as well as some build tools. An upstream link alone does
not satisfy every notice or source-availability obligation.

## 確認した原文と取得範囲

`licenses/inventory.json` はWindows x64 / Python 3.14.5の基本構成で導入したPython依存と、npmの画面ランタイム依存、計28パッケージの版・上流URL・原文ファイル・SHA-256を記録しています。原文は `licenses/python/` と `licenses/npm/` に保存しています。他CPU・他OS・開発・任意ML依存をすべて確認した一覧ではありません。

Pillow、NumPy、SciPyに同梱されたライブラリの条件は、各パッケージのライセンス原文を参照してください。

FreeTypeについてはFreeType Licenseを選択します。
**This software is based in part on the work of the FreeType Team.**
原文の著作権表示・ライセンス本文・追加条件を保持します。

今回のソースZIPにはランタイム・ネイティブバイナリ・モデル重みを同梱しません。初回取得物には上流の原文を保持します。これらを別途同梱・再配布する場合は、対応するソース提供や配布条件を対象版ごとに確認する必要があります。macOSランタイム、任意ML/CUDA、モデル重み、別方式のWindowsバイナリ候補は未確認条件が残っています。

## Backend runtime (locked in `backend/uv.lock`)

| Package | License | Notes |
|---|---|---|
| FastAPI | MIT | https://github.com/fastapi/fastapi |
| Starlette | BSD-3-Clause | FastAPI dependency |
| Pydantic / pydantic-core | MIT | https://github.com/pydantic/pydantic |
| Uvicorn (`uvicorn[standard]`) | BSD-3-Clause | Includes httptools, uvloop, watchfiles, websockets, python-dotenv, PyYAML (all permissive BSD/MIT-licensed) |
| Pillow 13.0.0 | MIT-CMU + 内包物の条件 | `licenses/python/pillow/pillow-13.0.0.dist-info/licenses/LICENSE` |
| ImageHash | BSD-2-Clause | https://github.com/JohannesBuchner/imagehash |
| NumPy | BSD-3-Clause | https://github.com/numpy/numpy |
| SciPy | BSD-3-Clause | ImageHash dependency; binary wheels may bundle OpenBLAS/LAPACK (BSD) and GCC runtime libraries under the GCC Runtime Library Exception |
| PyWavelets 1.9.0 | MIT AND BSD-3-Clause | ImageHash dependency; 原文は `licenses/python/PyWavelets/` |
| AnyIO, HTTPX, httpcore, h11, idna, certifi | MIT / BSD / ISC / MPL-2.0 (certifi) | HTTP stack; all permissive |
| Click, PyYAML, packaging, typing-extensions | MIT / Apache-2.0 / BSD | Transitive utilities |

Build backend pins `setuptools==83.0.0` and `wheel==0.47.0` (both MIT).

## Backend optional ML extras

The portable launcher installs these only with `--ml` (`cpu`) or `--cuda`.
The default setup does not include ML packages. Their exact
binary contents and redistribution terms remain to be verified:

| Package | License | Notes |
|---|---|---|
| torch / torchvision | BSD-3-Clause | https://github.com/pytorch/pytorch |
| open-clip-torch | MIT | https://github.com/mlfoundations/open_clip |
| timm, safetensors, huggingface-hub, ftfy, regex, tqdm | Apache-2.0 / MIT / BSD | OpenCLIP dependencies |
| NVIDIA CUDA wheels (`nvidia-*`, `cuda-*`, `triton`) | NVIDIA Software License / MIT / BSD | Pulled only via the official PyTorch CUDA 13.0 index for `ml-cu130`; see https://download.pytorch.org/whl/cu130 and each package's upstream license |

## Backend dev-only

pytest (MIT) and ruff (MIT) are development/test tools and are not part of the distributed runtime.

## Frontend runtime (locked in `frontend/package-lock.json`)

| Package | License |
|---|---|
| react / react-dom | MIT |
| lucide-react | ISC |

## Frontend build/dev (not shipped as source)

Vite, Vitest, TypeScript, Playwright, Testing Library, jsdom and their
transitive dependencies are build/test tools. Notable transitive licenses
observed in `frontend/package-lock.json`:

- MIT (majority, 150+ packages)
- Apache-2.0 (TypeScript, Playwright-related packages)
- ISC (several small utilities)
- BSD-2-Clause / BSD-3-Clause
- 0BSD (`tslib`), MIT-0 (`@csstools/color-helpers`)
- MPL-2.0 (`lightningcss` family, used by Vite as a build-time CSS engine)
- CC-BY-4.0 (`caniuse-lite`, browser compatibility data)

The production bundle in `frontend/dist/` contains compiled application code
plus MIT/ISC-licensed runtime libraries above; build-time engines and data
files are not embedded as independent copyleft works.

## How components are included

- クローン配布ではランタイムバイナリをGitに同梱しません。初回セットアップで公式配布元からuv（MIT / Apache-2.0）、CPython（PSF等のライセンス）、Node.js/npm（MIT等の内包ライセンス）を取得し、配布物内の原文ライセンスを保持します。固定版・取得物のハッシュは `scripts/portable-runtime.json` を参照してください。

- Backend packages are downloaded at setup using locked requirements.
  Downloads remain subject to their upstream terms. Any bundled runtime
  archive must preserve original license and notice files, verify native
  components, and meet applicable source-availability requirements.
- The production frontend bundle (`frontend/dist/`) compiles this project's
  own code together with the MIT/ISC runtime packages listed above.

## Copyright holders (runtime)

- FastAPI, annotated-doc: Sebastián Ramírez
- Starlette, Uvicorn, httpcore, httpx: Tom Christie
- Pydantic, pydantic-core, watchfiles, typing-inspection: Samuel Colvin and
  Pydantic contributors
- annotated-types: Adrian Garcia Badaracco, Samuel Colvin, Zac Hatfield-Dodds
- AnyIO: Alex Grönholm
- h11: Nathaniel J. Smith
- httptools: Yury Selivanov
- idna: Kim Davies
- certifi: Kenneth Reitz
- Pillow: Jeffrey A. Clark and the Pillow contributors
- ImageHash: Johannes Buchner
- NumPy: Travis E. Oliphant et al.
- SciPy: SciPy Developers
- PyWavelets: the PyWavelets contributors
- python-dotenv: Saurabh Kumar
- PyYAML: Kirill Simonov
- typing-extensions: Guido van Rossum, Jukka Lehtosalo, Łukasz Langa,
  Michael Lee
- packaging: Donald Stufft
- react, react-dom, scheduler: Meta Platforms, Inc. and affiliates
- lucide-react: the Lucide contributors

This author summary is not a substitute for the original copyright and
license notices. Full original texts must accompany redistributed code;
the final archive still needs that verification.

## Assets

No third-party images, fonts, audio, video, models, or sample pictures are
bundled in this repository. Test fixtures are generated programmatically
during tests. Users supply their own images at scan time.
