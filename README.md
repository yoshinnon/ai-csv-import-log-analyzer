# AI自律カバレッジ最適化エージェント

> CSVインポートエラーを **Gemini 3 Flash (Vertex AI)** が自律解析し、修正コード生成・テスト・PR作成・Slack通知を **完全自動化** するシステム。

---

## アーキテクチャ

```
┌──────────────┐   CSV エラー    ┌──────────────────────────────────────┐
│  他系システム  │ ──────────────► │         Cloud Run (FastAPI)           │
└──────────────┘                 │                                      │
                                 │  1. Gemini 3 Flash で根本原因解析      │
┌──────────────┐                 │  2. 修正コード & テストコード生成       │
│ Cloud Storage│ ◄── CSV保存 ──  │  3. pytest-cov で自律カバレッジ計測    │
│ (Landing Zone│                 │  4. 80%未満 → Gemini に追加テスト要求  │
└──────────────┘                 │     (最大5回ループ)                   │
                                 │  5. GitHub Issue/Branch/PR 自動作成   │
┌──────────────┐                 │  6. Slack Block Kit 通知              │
│  Cloud SQL   │ ◄── DDL参照 ──  └──────────────────────────────────────┘
│  (PostgreSQL)│                          │            │
└──────────────┘                          ▼            ▼
                                    ┌──────────┐  ┌──────────┐
                                    │  GitHub  │  │  Slack   │
                                    │ Issue/PR │  │ 通知     │
                                    └──────────┘  └──────────┘
```

## テクニカルスタック

| 層 | 技術 |
|----|------|
| AI Engine | Vertex AI – Gemini 3 Flash |
| Runtime | Cloud Run (Python 3.12 / FastAPI) |
| Storage | Cloud Storage (CSV Landing Zone) |
| Database | Cloud SQL (PostgreSQL 15) |
| IaC | Terraform 1.5+ |
| CI/CD | GitHub Actions + Workload Identity Federation |
| Secret管理 | Secret Manager |
| 通知 | Slack Block Kit |
| VCS連携 | PyGitHub (Issue / Branch / PR) |

---

## ディレクトリ構造
```
ai-csv-analyzer/
│
├── .github/
│   └── workflows/
│       └── deploy.yml          # Step 3・15: CI/CD + カバレッジStep Summary
│
├── src/
│   ├── __init__.py
│   ├── api/
│   │   ├── __init__.py
│   │   └── main.py             # FastAPI エントリポイント（全Stepオーケストレーター）
│   └── services/
│       ├── __init__.py
│       ├── gemini_analyzer.py  # Step 2・10・14: Gemini 3 Flash 解析・テスト再帰生成
│       ├── slack_notifier.py   # Step 5・6・9: Block Kit 通知・GitHubボタン
│       ├── github_client.py    # Step 8: Issue / Branch / PR 自動作成
│       └── test_runner.py      # Step 11・13・14: pytest-cov 自律ループ
│
├── terraform/
│   ├── main.tf                 # Step 1・4・7: 全GCPリソース・WIF・Secret Manager
│   ├── variables.tf
│   ├── outputs.tf
│   └── terraform.tfvars.example  # ← コピーして terraform.tfvars に rename して使用
│
├── tests/
│   ├── __init__.py
│   └── test_gemini_analyzer.py # システム自体のユニットテスト
│
├── .gitignore
├── Dockerfile
├── pytest.ini
├── requirements.txt
└── README.md                   # セットアップ手順・API使用例
```

## セットアップ手順

### 1. 前提条件

```bash
# 必要ツール
gcloud --version   # >= 460
terraform --version  # >= 1.5
python --version   # >= 3.12
```

### 2. GCP プロジェクト設定

```bash
export PROJECT_ID="your-project-id"
export REGION="asia-northeast1"

gcloud config set project $PROJECT_ID
gcloud auth application-default login
```

### 3. Terraform バックエンド用 GCS バケット作成

```bash
gsutil mb -l $REGION gs://${PROJECT_ID}-tf-state
```

### 4. terraform/main.tf の `backend "gcs"` を編集

```hcl
backend "gcs" {
  bucket = "YOUR_TF_STATE_BUCKET"  # ← 上で作成したバケット名
  prefix = "ai-csv-analyzer/state"
}
```

### 5. Terraform 実行

```bash
cd terraform
terraform init
terraform plan \
  -var="project_id=$PROJECT_ID" \
  -var="region=$REGION" \
  -var="github_repo=your-org/your-repo"

terraform apply ...
```

### 6. Secret Manager にシークレットを登録

```bash
# Slack Webhook URL
echo -n "https://hooks.slack.com/services/XXX/YYY/ZZZ" | \
  gcloud secrets versions add slack-webhook-url --data-file=-

# GitHub PAT (repo, workflow スコープ必要)
echo -n "ghp_xxxxxxxxxxxx" | \
  gcloud secrets versions add github-pat --data-file=-
```

### 7. GitHub Actions 変数設定

リポジトリの Settings > Variables に以下を追加:

| 変数名 | 値 |
|--------|----|
| `GCP_PROJECT_ID` | GCPプロジェクトID |
| `WIF_PROVIDER` | Terraform output `workload_identity_provider` の値 |
| `GCP_SA_EMAIL` | Terraform output `github_actions_sa_email` の値 |

### 8. ローカル開発環境

```bash
pip install -r requirements.txt

# 環境変数
export PROJECT_ID="your-project-id"
export REGION="asia-northeast1"
export GITHUB_REPO="your-org/your-repo"

uvicorn src.api.main:app --reload
```

---

## API 使用例

```bash
curl -X POST http://localhost:8080/analyze \
  -F "error_log=ValueError: invalid literal for int() with base 10: 'N/A'" \
  -F "source_code=df['user_id'] = df['user_id'].astype(int)" \
  -F "ddl=CREATE TABLE users (user_id INTEGER NOT NULL);" \
  -F "target_file_path=src/import_handler.py" \
  -F "csv_file=@./users.csv"
```

### レスポンス例

```json
{
  "root_cause": "`user_id` 列に文字列 'N/A' が含まれていますが、DBは整数型です。",
  "risk_level": "LOW",
  "estimated_hours": 0.5,
  "affected_tables": ["users", "import_log"],
  "fixed_code": "df['user_id'] = pd.to_numeric(df['user_id'], errors='coerce').fillna(0).astype(int)",
  "migration_steps": ["バックアップを取得", "型変換ロジックを追加", "ステージング環境でテスト", "本番デプロイ"],
  "impact_scope": "user_id が NULL になる行が存在する可能性あり",
  "test_coverage": 88.5,
  "test_passed": true,
  "github_issue_url": "https://github.com/org/repo/issues/42",
  "github_pr_url": "https://github.com/org/repo/pull/43"
}
```

---

## テスト実行

```bash
# 単体テスト
pytest tests/ -v

# カバレッジ付き (目標 80%)
pytest tests/ --cov=src --cov-report=term-missing --cov-fail-under=80
```

---

## GCP 採用の技術的根拠

| 観点 | 詳細 |
|------|------|
| **セキュリティ** | Vertex AI エンタープライズ規約によりソースコード・ログが学習データに二次利用されない |
| **ゼロトラスト** | Workload Identity Federation でキーレス認証を実現。JSONキーの漏洩リスクを構造的に排除 |
| **コスト** | Gemini 3 Flash は高精度・低単価。Cloud Run のサーバーレスで待機コストゼロ |
| **運用** | Cloud Logging / Error Reporting との親和性が高くグルーコード最小化 |

---

## Slack 通知イメージ

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🚨 データ連携エラー解析報告

📁 対象ファイル: users.csv    🟢 リスクレベル: LOW

🔍 根本原因
`user_id` 列に文字列 'N/A' が含まれていますが、DBは整数型です。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🛠 修正コード
  df['user_id'] = pd.to_numeric(df['user_id'], errors='coerce').fillna(0).astype(int)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⏳ 工数見積: 0.5h ｜ 🟢 リスク: LOW ｜ 🤖 Gemini 3 Flash

✅ AI 実行テスト結果 ｜ 📊 カバレッジ: 88.5%

[🐛 Issue #42 を確認]  [🔀 PR #43 をレビュー]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```
