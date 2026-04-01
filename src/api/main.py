"""
src/api/main.py
FastAPI エントリポイント – 全 Step のオーケストレーター
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.services.gemini_analyzer import AnalysisRequest, GeminiAnalyzer
from src.services.github_client import GitHubClient
from src.services.slack_notifier import GitHubLinks, SlackNotifier
from src.services.test_runner import TestRunner

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# ── 環境変数 ─────────────────────────────────────────────────────────────────
PROJECT_ID = os.environ["PROJECT_ID"]
REGION     = os.environ.get("REGION", "asia-northeast1")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")

# ── DI コンテナ (シングルトン) ───────────────────────────────────────────────
_gemini: GeminiAnalyzer | None = None
_slack: SlackNotifier | None = None
_github: GitHubClient | None = None
_test_runner: TestRunner | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _gemini, _slack, _github, _test_runner
    logger.info("サービス初期化中…")
    _gemini = GeminiAnalyzer(project_id=PROJECT_ID, region=REGION)
    _slack = SlackNotifier(project_id=PROJECT_ID)
    if GITHUB_REPO:
        _github = GitHubClient(project_id=PROJECT_ID, repo_name=GITHUB_REPO)
    _test_runner = TestRunner(gemini_analyzer=_gemini)
    logger.info("初期化完了")
    yield


app = FastAPI(
    title="AI自律カバレッジ最適化エージェント",
    version="5.0.0",
    lifespan=lifespan,
)


# ─────────────────────────────────────────────────────────────────────────────
# リクエスト / レスポンス モデル
# ─────────────────────────────────────────────────────────────────────────────

class AnalyzeResponse(BaseModel):
    root_cause: str
    risk_level: str
    estimated_hours: float
    affected_tables: list[str]
    fixed_code: str
    migration_steps: list[str]
    impact_scope: str
    test_coverage: float | None = None
    test_passed: bool | None = None
    coverage_table: str | None = None
    github_issue_url: str | None = None
    github_pr_url: str | None = None


# ─────────────────────────────────────────────────────────────────────────────
# ヘルスチェック
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "version": "5.0.0"}


# ─────────────────────────────────────────────────────────────────────────────
# メイン解析エンドポイント
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze(
    background_tasks: BackgroundTasks,
    error_log: Annotated[str, Form()],
    source_code: Annotated[str, Form()],
    ddl: Annotated[str, Form()],
    target_file_path: Annotated[str, Form()] = "src/import_handler.py",
    csv_file: UploadFile | None = File(default=None),
):
    """
    CSVインポートエラーを Gemini 3 Flash で解析し、
    修正案・テスト生成・GitHub PR・Slack通知を自動実行する
    """
    # CSV サンプル読み込み
    csv_sample = ""
    file_name = "unknown.csv"
    if csv_file:
        raw = await csv_file.read()
        lines = raw.decode("utf-8", errors="replace").splitlines()
        csv_sample = "\n".join(lines[:6])
        file_name = csv_file.filename or "upload.csv"

    # ── Step 2: Gemini 解析 ──────────────────────────────────────────
    req = AnalysisRequest(
        error_log=error_log,
        source_code=source_code,
        ddl=ddl,
        csv_sample=csv_sample,
        file_name=file_name,
    )
    try:
        result = _gemini.analyze(req)
    except Exception as exc:
        logger.exception("Gemini 解析エラー")
        raise HTTPException(status_code=500, detail=f"Gemini 解析失敗: {exc}") from exc

    # ── Step 10 + 11 + 13 + 14: テスト自律ループ ────────────────────
    test_run = None
    try:
        test_run = _test_runner.run_with_coverage_loop(
            source_code=result.fixed_code,
            test_code=result.test_code,
            source_file_name=target_file_path.split("/")[-1],
        )
        logger.info(
            "テストループ完了: passed=%s iterations=%d",
            test_run.passed,
            test_run.iterations,
        )
    except Exception as exc:
        logger.warning("テスト実行中にエラー (継続): %s", exc)

    # カバレッジ集計
    coverage_pct: float | None = None
    coverage_table = ""
    if test_run and test_run.coverage_reports:
        reps = test_run.coverage_reports
        coverage_pct = sum(r.percent_covered for r in reps) / len(reps)
        coverage_table = test_run.to_coverage_table()

    # ── Step 8: GitHub Issue / Branch / PR 作成 ─────────────────────
    github_links: GitHubLinks | None = None
    if _github:
        try:
            artifacts = _github.create_fix_artifacts(
                error_summary=error_log[:500],
                root_cause=result.root_cause,
                fixed_code=result.fixed_code,
                migration_steps=result.migration_steps,
                risk_level=result.risk_level,
                estimated_hours=result.estimated_hours,
                target_file_path=target_file_path,
                test_code=test_run.test_code_used if test_run else result.test_code,
                test_results=test_run.to_summary_text() if test_run else "",
                coverage_table=coverage_table,
            )
            github_links = GitHubLinks(
                issue_url=artifacts.issue_url,
                issue_number=artifacts.issue_number,
                pr_url=artifacts.pr_url,
                pr_number=artifacts.pr_number,
                branch_name=artifacts.branch_name,
            )
        except Exception as exc:
            logger.warning("GitHub 連携エラー (継続): %s", exc)

    # ── Step 5 + 9: Slack 通知 (非同期) ─────────────────────────────
    background_tasks.add_task(
        _slack.notify_analysis_result,
        file_name=file_name,
        root_cause=result.root_cause,
        fixed_code=result.fixed_code,
        migration_steps=result.migration_steps,
        risk_level=result.risk_level,
        estimated_hours=result.estimated_hours,
        github_links=github_links,
        test_summary=test_run.to_summary_text() if test_run else "",
        coverage_percent=coverage_pct,
    )

    return AnalyzeResponse(
        root_cause=result.root_cause,
        risk_level=result.risk_level,
        estimated_hours=result.estimated_hours,
        affected_tables=result.affected_tables,
        fixed_code=result.fixed_code,
        migration_steps=result.migration_steps,
        impact_scope=result.impact_scope,
        test_coverage=coverage_pct,
        test_passed=test_run.passed if test_run else None,
        coverage_table=coverage_table or None,
        github_issue_url=github_links.issue_url if github_links else None,
        github_pr_url=github_links.pr_url if github_links else None,
    )
