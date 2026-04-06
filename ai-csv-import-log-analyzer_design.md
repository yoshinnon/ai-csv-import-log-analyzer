
**システム設計書**

**AI 自律カバレッジ最適化エージェント**

Ver. 5.0


|**項目**|**内容**|
| -- | -- |
|システム名|AI自律カバレッジ最適化エージェント|
|バージョン|Ver. 5.0|
|作成日|2026/4/5|
|対象環境|Google Cloud Platform (GCP)|
|AI エンジン|Vertex AI – Gemini 3 Flash|
|ランタイム|Cloud Run (Python 3.12 / FastAPI)|
|IaC|Terraform 1.5+|
|CI/CD|GitHub Actions + Workload Identity Federation|



AI 自律カバレッジ最適化エージェント　システム設計書　Ver. 5.0
# **目次**

# **1. システム概要**
## **1.1 目的**
本システムは、他系システムから連携される CSV データのインポートエラー（型不整合等）が発生した際、Gemini 3 Flash が「エンジニアの初見解析」を代行するシステムである。

エラーログ・ソースコード・DB 定義（DDL）をリアルタイムに相関分析し、具体的な改修案とリスク判定を出力する。さらに、修正コードの品質を pytest-cov による自律的なカバレッジ計測で担保し、GitHub への PR 作成・Slack 通知までを無人で完結させる。

## **1.2 対象範囲**
- CSV インポートエラーの自動解析（型不整合・NULL 違反・フォーマット不備 等）
- 修正コード・移行手順・リスク判定の自動生成
- pytest による自律テスト生成とカバレッジ自己補完ループ（目標 80%）
- GitHub Issue / Branch / Pull Request の自動作成
- Slack Block Kit による解析結果通知（GitHub リンク付き）

## **1.3 システム全体像**
以下にシステムの処理フローを示す。

|**フェーズ**|**処理内容**|**使用サービス**|
| -- | -- | -- |
|① エラー検知|CSV インポート失敗を検知しエンドポイントへ通知|Cloud Run (FastAPI)|
|② AI 解析|エラーログ・DDL・ソースコードを Gemini 3 Flash へ投入|Vertex AI|
|③ テスト生成|修正コードに対する pytest テストコードを自動生成|Gemini 3 Flash|
|④ 自律ループ|カバレッジ計測 → 不足分を Gemini に補完依頼（最大 5 回）|pytest-cov|
|⑤ GitHub 連携|Issue 作成 → ブランチ作成 → コード更新 → PR 作成|PyGitHub|
|⑥ Slack 通知|Block Kit 形式で解析結果・GitHub リンクを通知|Slack Webhook|

# **2. アーキテクチャ**
## **2.1 テクニカルスタック**

|**レイヤー**|**コンポーネント**|**バージョン / 仕様**|
| -- | -- | -- |
|AI エンジン|Vertex AI – Gemini 3 Flash|最新安定版|
|ランタイム|Cloud Run (Python / FastAPI)|Python 3.12 / FastAPI 0.115|
|ストレージ|Cloud Storage (CSV Landing Zone)|asia-northeast1|
|データベース|Cloud SQL – PostgreSQL 15|db-g1-small / ZONAL|
|IaC|Terraform|1.5+|
|CI/CD|GitHub Actions|WIF 認証（キーレス）|
|Secret 管理|Secret Manager|Slack Webhook URL / GitHub PAT|
|通知|Slack Block Kit|Webhook POST|
|VCS 連携|PyGitHub|2.4.0|
|テスト|pytest + pytest-cov|8.3+ / 5.0+|

## **2.2 GCP 採用根拠**
### **2.2.1 セキュリティ（データガバナンス）**
Vertex AI のエンタープライズ規約により、入力されたソースコードや機密ログがモデルの学習に二次利用されない。個人向け Gemini 契約や他社 API と比較して、ガバナンス上の絶対的な優位性を持つ。

### **2.2.2 ゼロトラスト認証**
Workload Identity Federation（WIF）により、GitHub Actions 側に永続的なサービスアカウントキー（JSON）を置かない。漏洩リスクを構造的に排除したモダンな CI/CD パイプラインを実現する。

### **2.2.3 コスト対パフォーマンス**
Gemini 3 Flash の採用により、高い推論精度を極めて低いトークン単価で享受できる。Cloud Run のサーバーレス特性と合わせ、低コストでの 24 時間常駐エンジニア環境を構築する。

### **2.2.4 運用自動化との親和性**
Google 独自の Cloud Logging / Error Reporting との親和性が高く、エラー検知から AI 解析発火までのパイプラインを最小限のグルーコードで実装可能である。

## **2.3 ディレクトリ構造**
リポジトリのディレクトリ構造を以下に示す。

ai-csv-analyzer/

├── .github/

│   └── workflows/

│       └── deploy.yml          # CI/CD + カバレッジ Step Summary (Step 3・15)

├── src/

│   ├── api/

│   │   └── main.py             # FastAPI エントリポイント（全 Step オーケストレーター）

│   └── services/

│       ├── gemini_analyzer.py  # Gemini 3 Flash 解析・テスト再帰生成 (Step 2・10・14)

│       ├── slack_notifier.py   # Block Kit 通知・GitHub ボタン (Step 5・6・9)

│       ├── github_client.py    # Issue / Branch / PR 自動作成 (Step 8)

│       └── test_runner.py      # pytest-cov 自律カバレッジループ (Step 11・13・14)

├── terraform/

│   ├── main.tf                 # 全 GCP リソース・WIF・Secret Manager (Step 1・4・7)

│   ├── variables.tf

│   └── outputs.tf

├── tests/

│   └── test_gemini_analyzer.py # システム自体のユニットテスト

├── Dockerfile

├── pytest.ini

├── requirements.txt

└── README.md

# **3. 機能設計**
## **3.1 エラー解析エンジン（gemini_analyzer.py）**
Vertex AI の Gemini 3 Flash モデルを使用し、以下の情報を相関分析して JSON 形式で修正案を出力する。

|**入力**|**説明**|
| -- | -- |
|error_log|インポート時に発生したエラーログ（スタックトレース含む）|
|source_code|インポート処理の対象 Python ソースコード|
|ddl|インポート先テーブルの CREATE 文（DDL）|
|csv_sample|エラーが発生した CSV の先頭 5 行（任意）|

|**出力フィールド**|**型**|**説明**|
| -- | -- | -- |
|root_cause|string|エラーの根本原因（日本語）|
|fixed_code|string|修正後の完全な Python コード|
|migration_steps|string[]|段階的な移行手順リスト|
|impact_scope|string|影響範囲の説明|
|risk_level|LOW〜CRITICAL|リスク判定（4 段階）|
|estimated_hours|number|修正工数見積もり（時間）|
|affected_tables|string[]|影響を受けるテーブル名リスト|
|test_code|string|修正を検証する pytest テストコード|

## **3.2 自律カバレッジ最適化ループ（test_runner.py）**
修正コードに対して pytest-cov を実行し、カバレッジが閾値（80%）に達するまで Gemini に追加テストを要求するループ処理を実装する。

|**パラメータ**|**値**|**説明**|
| -- | -- | -- |
|COVERAGE_THRESHOLD|80.0 %|カバレッジ達成目標|
|MAX_COVERAGE_RETRIES|5 回|最大反復回数（超過時は Issue に理由を記録）|
|cov-report 形式|JSON + term-missing + HTML|解析用 + 人間向け + PR 表示用|

ループのアルゴリズムを以下に示す。

1. Initial Test: Gemini 3 Flash が初回テストコードを生成。
1. Execution & Analysis: pytest --cov を実行し、JSON レポートから percent_covered を抽出。
1. Conditional Branch: 80% 以上 → ループ終了。80% 未満 → 未通過行番号を Gemini に提示して追加テストを要求。
1. Append: 既存テストを破壊せず追加テストを Append。最大 5 回まで反復。
1. Fallback: 達成できない場合はその理由を GitHub Issue に記録して停止。

## **3.3 Slack 通知（slack_notifier.py）**
Gemini の解析結果を Slack Block Kit 形式で通知する。Secret Manager から Webhook URL を取得して POST する。

|**Block タイプ**|**表示内容**|
| -- | -- |
|Header|🚨 データ連携エラー解析報告|
|Section|対象ファイル名・リスクレベル|
|Section|根本原因の要約|
|Section|修正コード（Markdown コードブロック）|
|Section|移行手順|
|Context|工数見積・リスクレベル・解析エンジン名|
|Section|AI 実行テスト結果・カバレッジ率（任意）|
|Actions|GitHub Issue ボタン・PR レビューボタン（任意）|

## **3.4 GitHub 連携（github_client.py）**
PyGitHub ライブラリを使用して、解析結果を元に以下を順番に自動実行する。

1. GitHub Issue を作成（AI生成ラベル・リスクレベルラベル付き）
1. fix/ai-analysis-[issue_id] ブランチを作成
1. 修正コードで対象ファイルを更新
1. closes #[issue_id] を説明に含めた Pull Request を作成
1. PR 本文にカバレッジテーブル・テスト結果・AI 生成テストコードを自動記載

# **4. インフラ設計（Terraform）**
## **4.1 GCP リソース一覧**

|**リソース**|**Terraform ID**|**用途**|
| -- | -- | -- |
|Cloud Run v2|google_cloud_run_v2_service.ai_analyzer|FastAPI アプリ実行基盤|
|Cloud SQL (PostgreSQL)|google_sql_database_instance.main|インポート先 DB|
|Cloud Storage|google_storage_bucket.csv_landing|CSV Landing Zone|
|Secret Manager|google_secret_manager_secret.\*|Slack Webhook / GitHub PAT / DB PW|
|Workload Identity Pool|google_iam_workload_identity_pool.github|GitHub Actions キーレス認証|
|Service Account (CR)|google_service_account.cloud_run_sa|Cloud Run 実行 SA|
|Service Account (GHA)|google_service_account.github_actions_sa|GitHub Actions デプロイ SA|
|Artifact Registry|google_artifact_registry_repository.app|Docker イメージリポジトリ|
|VPC / Subnet|google_compute_network.vpc|Cloud SQL プライベート接続|

## **4.2 Secret Manager 管理シークレット**

|**Secret ID**|**格納内容**|**アクセス主体**|
| -- | -- | -- |
|slack-webhook-url|Slack Incoming Webhook URL|Cloud Run SA|
|github-pat|GitHub Personal Access Token (repo・workflow スコープ)|Cloud Run SA|
|db-password|Cloud SQL ユーザーパスワード（Terraform 自動生成）|Cloud Run SA|

## **4.3 IAM 設計**
Cloud Run サービスアカウントに付与するロールを以下に示す。

|**ロール**|**目的**|
| -- | -- |
|roles/aiplatform.user|Vertex AI (Gemini) 呼び出し|
|roles/cloudsql.client|Cloud SQL への接続|
|roles/storage.objectViewer|Cloud Storage (CSV) の読み取り|
|roles/secretmanager.secretAccessor|Secret Manager のシークレット読み取り|
|roles/logging.logWriter|Cloud Logging への書き込み|
|roles/cloudtrace.agent|Cloud Trace へのトレース送信|

# **5. CI/CD 設計（GitHub Actions）**
## **5.1 ワークフロー概要**
deploy.yml に定義されたワークフローは、以下の 4 ジョブで構成される。

|**ジョブ名**|**トリガー**|**処理内容**|
| -- | -- | -- |
|terraform|push / PR (main)|Terraform Plan / Apply によるインフラ反映|
|build|push (main) のみ|Docker イメージビルド → Artifact Registry へ Push|
|deploy|push (main) のみ|Cloud Run へのデプロイ → ヘルスチェック → Slack 通知|
|test|PR のみ|pytest-cov 実行 → Step Summary 出力 → PR コメント投稿|

## **5.2 Workload Identity Federation 認証フロー**
1. GitHub Actions が OIDC トークンを生成（JWT）
1. WIF Provider が JWT を検証（issuer: token.actions.githubusercontent.com）
1. attribute.repository 条件でリポジトリを限定
1. github_actions_sa への impersonate が許可され、一時的な Google 認証情報を取得
1. JSON キーの生成・保存が不要（ゼロトラスト）

## **5.3 Step 15: カバレッジ Step Summary**
テストジョブ実行時、GitHub Actions の Step Summary タブに以下を出力する。

|**出力項目**|**内容**|
| -- | -- |
|全体カバレッジ|達成率（%）・プログレスバー（█ 形式）・ステートメント数|
|ファイル別テーブル|ファイルパス・カバレッジ率・実行済み行数・未実行行番号・✅/⚠️ステータス|
|pytest 出力|末尾 120 行のターミナル出力|

PR コメントにはコンパクト版テーブルと Step Summary への直リンクを投稿する。<!-- ai-coverage-report --> マーカーにより重複投稿を防止し、プッシュのたびに上書き更新する。

# **6. API 仕様**
## **6.1 エンドポイント一覧**

|**メソッド**|**パス**|**説明**|
| -- | -- | -- |
|GET|/health|ヘルスチェック（Cloud Run の起動確認に使用）|
|POST|/analyze|CSV インポートエラーの解析・修正・通知を実行|

## **6.2 POST /analyze リクエスト**
Content-Type: multipart/form-data

|**パラメータ**|**型**|**必須**|**説明**|
| -- | -- | -- | -- |
|error_log|string (Form)|○|インポートエラーログ|
|source_code|string (Form)|○|インポート処理のソースコード|
|ddl|string (Form)|○|インポート先テーブルの DDL|
|target_file_path|string (Form)|×|修正対象ファイルパス（デフォルト: src/import_handler.py）|
|csv_file|file (Upload)|×|エラーが発生した CSV ファイル（サンプル抽出用）|

## **6.3 POST /analyze レスポンス**

|**フィールド**|**型**|**説明**|
| -- | -- | -- |
|root_cause|string|Gemini が分析した根本原因|
|risk_level|string|LOW / MEDIUM / HIGH / CRITICAL|
|estimated_hours|number|修正工数見積もり（時間）|
|affected_tables|string[]|影響テーブル名リスト|
|fixed_code|string|修正後の Python コード|
|migration_steps|string[]|移行手順リスト|
|impact_scope|string|影響範囲の説明|
|test_coverage|number?|達成されたカバレッジ率（%）|
|test_passed|bool?|テストが全件パスしたか|
|coverage_table|string?|Markdown 形式のカバレッジテーブル|
|github_issue_url|string?|作成された GitHub Issue の URL|
|github_pr_url|string?|作成された GitHub PR の URL|

# **7. 非機能要件**
## **7.1 セキュリティ**

|**要件**|**実装方法**|
| -- | -- |
|機密データ保護|Vertex AI エンタープライズ規約によりソースコード・ログが学習データに利用されない|
|認証キーレス化|Workload Identity Federation で GitHub Actions に JSON キーを配置しない|
|シークレット管理|DB パスワード・Webhook URL・PAT を Secret Manager で一元管理|
|ネットワーク隔離|Cloud SQL は VPC 内のプライベート IP のみで接続（パブリック IP 無効）|
|最小権限|Cloud Run SA / GitHub Actions SA にそれぞれ必要最小限のロールのみ付与|

## **7.2 可用性・パフォーマンス**

|**指標**|**設計値**|**実装方法**|
| -- | -- | -- |
|タイムアウト|300 秒|Cloud Run timeout 設定|
|最大インスタンス|10|Cloud Run スケーリング設定|
|最小インスタンス|0（コールドスタート）|コスト最適化|
|カバレッジ閾値|80%|pytest --cov-fail-under=80|
|最大テスト反復|5 回|MAX_COVERAGE_RETRIES 定数|

## **7.3 テストの品質保証**
- アサーション必須: Gemini へのプロンプトに「具体的な assert 文を記述すること」を明示
- 境界値テスト: 追加テスト要求時に「境界値テストを含む」を厳命
- 無意味なテストの排除: カバレッジを水増しするだけのテストを構造的に防止
- 可視性: どのテストケースがカバレッジ向上に寄与したかをログに記録

## **7.4 運用・監視**
- Cloud Logging: 全処理ログを構造化して出力（JSON 形式）
- Cloud Trace: FastAPI の各リクエストをトレース
- Slack 通知: エラー発生時・解析完了時・デプロイ時に即時通知
- GitHub PR: レビュー待ち状態で人間の最終承認を必須とする設計

# **8. セットアップ手順**
## **8.1 前提条件**

|**ツール**|**必要バージョン**|**確認コマンド**|
| -- | -- | -- |
|gcloud CLI|>= 460|gcloud --version|
|Terraform|>= 1.5|terraform --version|
|Python|>= 3.12|python --version|
|Docker|最新安定版|docker --version|

## **8.2 初期セットアップ**
1. GCP プロジェクトの作成とログイン
```
gcloud config set project YOUR_PROJECT_ID
gcloud auth application-default login
```

1. Terraform バックエンド用 GCS バケットを作成
```
gsutil mb -l asia-northeast1 gs://YOUR_PROJECT_ID-tf-state
```

1. terraform/terraform.tfvars.example を terraform.tfvars にリネームして編集
```
project_id  = "your-gcp-project-id"
region      = "asia-northeast1"
github_repo = "your-org/your-repo"
```

1. Terraform を実行してインフラを構築
```
cd terraform && terraform init
terraform plan -out=tfplan
terraform apply tfplan
```

1. Secret Manager にシークレットを登録

echo -n "https://hooks.slack.com/..." | gcloud secrets versions add slack-webhook-url --data-file=-

echo -n "ghp_xxxx" | gcloud secrets versions add github-pat --data-file=-

1. GitHub リポジトリの Settings → Variables に登録
- GCP_PROJECT_ID: GCP プロジェクト ID
- WIF_PROVIDER: terraform output workload_identity_provider の値
- GCP_SA_EMAIL: terraform output github_actions_sa_email の値

## **8.3 ローカル開発環境**
```
pip install -r requirements.txt
export PROJECT_ID="your-project-id"
export GITHUB_REPO="your-org/your-repo"
uvicorn src.api.main:app --reload
```

# **9. 改訂履歴**

|**バージョン**|**日付**|**変更内容**|**担当**|
| -- | -- | -- | -- |
|1.0|—|初版作成（基本エラー解析機能）|AI Agent|
|2.0|—|GitHub Issue / PR 自動作成機能を追加|AI Agent|
|3.0|—|pytest テスト自動生成機能を追加|AI Agent|
|4.0|—|pytest-cov 自律カバレッジループを追加|AI Agent|
|5.0|2026/4/5|Step Summary 出力・PR コメント更新対応・設計書作成|AI Agent|

\-  -
