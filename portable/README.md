# Windowsポータブル候補（配布審査中）

この構成は、同梱PythonでAPIとビルド済みWebUIを同じループバックポートから配信します。起動時にNode・npm・uvを実行しません。Windows x64の通常権限とブラウザーが必要です。

## 利用方法

候補フォルダの `Start.bat` を起動します。コンソールを閉じるとアプリを終了します。ログイン情報は短命のローカル受け渡しファイルから交換されます。更新・移動・バックアップの前にアプリを終了してください。

- `runtime/`: Pythonとバックエンド依存
- `backend/`: アプリのコード
- `frontend/`: ビルド済み画面
- `licenses/`: バージョンと原文ライセンスの一覧
- `data/`: 実行時に作成するDB・APIトークン。第三者へ配布しない
- `cache/`: 実行時キャッシュ・一時ファイル

スキャン対象画像は選択された元の場所にあります。アプリフォルダを移動しても画像パスを書き換えません。画像も移動した場合は再スキャンしてください。更新時は `data/` を保全し、未知のDB版を旧アプリで開かないでください。

現在の試験候補は基本の画像照合機能向けで、PyTorch/OpenCLIP/CUDAやモデル重みは同梱していません。これらの追加構成はまだ配布条件を確認していません。ソースをクローンして配布する版は、リポジトリルートの起動スクリプトからランタイムを自動取得します。[クローン配布手順](../docs/portable_clone.md) を参照してください。

## 開発者向け組立手順

リポジトリルートから、ロック済みの本番依存環境を `.release-work/production-env` に作ります。開発用環境を指定したまま上書きしないでください。

```powershell
$env:UV_PROJECT_ENVIRONMENT = "$PWD\.release-work\production-env"
$env:UV_CACHE_DIR = "$PWD\.uv-cache"
uv sync --project backend --locked --no-dev --python 3.12
$env:VITE_API_BASE = '/api'
npm run build --prefix frontend
```

組立ツールには、原文ライセンス付きの完全なCPython 3.12 Windows x64ランタイムと、同じPython版の本番依存を明示します。

```powershell
.venv\Scripts\python.exe backend\tools\assemble_portable_candidate.py `
  --runtime <検証済みCPythonのフォルダ> `
  --site-packages .release-work\production-env\Lib\site-packages `
  --output .release-work\portable-win-x64-review
```

この組立は候補作成です。取得物の公式ハッシュ照合、ネイティブ内包物・ランタイムの再配布条件、モデル追加構成、クリーンPC検証、ZIP固定とハッシュ記録を終えるまで「配布準備完了」と扱わないでください。
