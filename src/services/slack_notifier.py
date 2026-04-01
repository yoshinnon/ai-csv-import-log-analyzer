"""
src/services/slack_notifier.py
Step 5 / Step 9: Slack Block Kit 通知サービス
Secret Manager から Webhook URL を取得して通知を送信する
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import requests
from google.cloud import secretmanager

logger = logging.getLogger(__name__)


@dataclass
class GitHubLinks:
    """Step 9: Issue / PR へのリンク情報"""
    issue_url: str = ""
    issue_number: int = 0
    pr_url: str = ""
    pr_number: int = 0
    branch_name: str = ""


class SlackNotifier:
    """Slack Block Kit を使用した通知クライアント"""

    _RISK_EMOJI = {
        "LOW": "🟢",
        "MEDIUM": "🟡",
        "HIGH": "🔴",
        "CRITICAL": "🆘",
    }

    def __init__(self, project_id: str, secret_id: str = "slack-webhook-url") -> None:
        self._webhook_url = self._fetch_secret(project_id, secret_id)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def notify_analysis_result(
        self,
        *,
        file_name: str,
        root_cause: str,
        fixed_code: str,
        migration_steps: list[str],
        risk_level: str,
        estimated_hours: float,
        github_links: GitHubLinks | None = None,
        test_summary: str = "",
        coverage_percent: float | None = None,
    ) -> bool:
        """
        Gemini 解析結果を Slack Block Kit 形式で通知する
        Step 9: GitHub Issue / PR へのボタンを追加
        """
        blocks = self._build_blocks(
            file_name=file_name,
            root_cause=root_cause,
            fixed_code=fixed_code,
            migration_steps=migration_steps,
            risk_level=risk_level,
            estimated_hours=estimated_hours,
            github_links=github_links,
            test_summary=test_summary,
            coverage_percent=coverage_percent,
        )
        return self._post(blocks)

    # ------------------------------------------------------------------
    # Block Kit ビルダー
    # ------------------------------------------------------------------

    def _build_blocks(
        self,
        *,
        file_name: str,
        root_cause: str,
        fixed_code: str,
        migration_steps: list[str],
        risk_level: str,
        estimated_hours: float,
        github_links: GitHubLinks | None,
        test_summary: str,
        coverage_percent: float | None,
    ) -> list[dict]:
        risk_emoji = self._RISK_EMOJI.get(risk_level, "⚠️")
        steps_text = "\n".join(
            f"{i + 1}. {step}" for i, step in enumerate(migration_steps)
        )

        blocks: list[dict] = [
            # ── Header ──────────────────────────────────────────────
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "🚨 データ連携エラー解析報告",
                    "emoji": True,
                },
            },
            {"type": "divider"},
            # ── ファイル名 & リスク ──────────────────────────────────
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*📁 対象ファイル*\n`{file_name}`",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*{risk_emoji} リスクレベル*\n{risk_level}",
                    },
                ],
            },
            # ── 原因の要約 ───────────────────────────────────────────
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*🔍 根本原因*\n{root_cause}",
                },
            },
            {"type": "divider"},
            # ── 修正コード ───────────────────────────────────────────
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*🛠 修正コード*\n```python\n{self._truncate(fixed_code, 800)}\n```",
                },
            },
            {"type": "divider"},
            # ── 移行手順 ─────────────────────────────────────────────
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*📋 移行手順*\n{steps_text}",
                },
            },
            {"type": "divider"},
            # ── 工数 & リスク Context ────────────────────────────────
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"⏳ *工数見積:* {estimated_hours}h　｜　"
                                f"{risk_emoji} *リスク:* {risk_level}　｜　"
                                f"🤖 *解析: Gemini 3 Flash (Vertex AI)*",
                    }
                ],
            },
        ]

        # ── テスト結果 (Step 12) ─────────────────────────────────────
        if test_summary:
            cov_text = (
                f"　｜　📊 *カバレッジ:* {coverage_percent:.1f}%"
                if coverage_percent is not None
                else ""
            )
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*✅ AI 実行テスト結果*{cov_text}\n```\n{self._truncate(test_summary, 600)}\n```",
                    },
                }
            )

        # ── GitHub ボタン (Step 9) ───────────────────────────────────
        if github_links and (github_links.issue_url or github_links.pr_url):
            action_elements: list[dict] = []
            if github_links.issue_url:
                action_elements.append(
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": f"🐛 Issue #{github_links.issue_number} を確認",
                            "emoji": True,
                        },
                        "url": github_links.issue_url,
                        "style": "danger",
                    }
                )
            if github_links.pr_url:
                action_elements.append(
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": f"🔀 PR #{github_links.pr_number} をレビュー",
                            "emoji": True,
                        },
                        "url": github_links.pr_url,
                        "style": "primary",
                    }
                )
            blocks.append({"type": "actions", "elements": action_elements})

        return blocks

    # ------------------------------------------------------------------
    # HTTP 送信
    # ------------------------------------------------------------------

    def _post(self, blocks: list[dict]) -> bool:
        payload = {"blocks": blocks}
        try:
            resp = requests.post(
                self._webhook_url,
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            resp.raise_for_status()
            logger.info("Slack 通知送信完了: status=%s", resp.status_code)
            return True
        except requests.RequestException as exc:
            logger.error("Slack 通知失敗: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _fetch_secret(project_id: str, secret_id: str) -> str:
        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("utf-8").strip()

    @staticmethod
    def _truncate(text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + "\n... (省略)"
