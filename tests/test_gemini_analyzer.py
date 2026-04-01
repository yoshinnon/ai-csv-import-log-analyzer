"""
tests/test_gemini_analyzer.py
システム自体のユニットテスト（pytest）
Gemini / Secret Manager はモック化して単体テストを実行
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.services.gemini_analyzer import (
    AnalysisRequest,
    AnalysisResult,
    GeminiAnalyzer,
)
from src.services.slack_notifier import GitHubLinks, SlackNotifier
from src.services.test_runner import CoverageReport, TestRunner


# ─────────────────────────────────────────────────────────────────────────────
# フィクスチャ
# ─────────────────────────────────────────────────────────────────────────────

SAMPLE_GEMINI_RESPONSE = {
    "root_cause": "`user_id` 列に文字列 'N/A' が含まれていますが、DBは整数型です。",
    "fixed_code": (
        "import pandas as pd\n\n"
        "def load_csv(path: str) -> pd.DataFrame:\n"
        "    df = pd.read_csv(path)\n"
        "    df['user_id'] = pd.to_numeric(df['user_id'], errors='coerce').fillna(0).astype(int)\n"
        "    return df\n"
    ),
    "migration_steps": [
        "バックアップを取得",
        "型変換ロジックを追加",
        "ステージング環境でテスト",
        "本番デプロイ",
    ],
    "impact_scope": "user_id が NULL になる行が存在する可能性あり",
    "risk_level": "LOW",
    "estimated_hours": 0.5,
    "affected_tables": ["users", "import_log"],
    "test_code": (
        "import pytest\n"
        "import pandas as pd\n\n"
        "def test_user_id_conversion_normal():\n"
        "    df = pd.DataFrame({'user_id': ['1', '2', '3']})\n"
        "    df['user_id'] = pd.to_numeric(df['user_id'], errors='coerce').fillna(0).astype(int)\n"
        "    assert list(df['user_id']) == [1, 2, 3]\n\n"
        "def test_user_id_conversion_na():\n"
        "    df = pd.DataFrame({'user_id': ['N/A', '42']})\n"
        "    df['user_id'] = pd.to_numeric(df['user_id'], errors='coerce').fillna(0).astype(int)\n"
        "    assert df['user_id'].iloc[0] == 0\n"
        "    assert df['user_id'].iloc[1] == 42\n"
    ),
}


@pytest.fixture
def mock_analysis_result() -> AnalysisResult:
    return AnalysisResult.from_dict(SAMPLE_GEMINI_RESPONSE)


@pytest.fixture
def sample_request() -> AnalysisRequest:
    return AnalysisRequest(
        error_log="ValueError: invalid literal for int() with base 10: 'N/A'",
        source_code="df['user_id'] = df['user_id'].astype(int)",
        ddl="CREATE TABLE users (user_id INTEGER NOT NULL);",
        csv_sample="user_id,name\nN/A,Alice\n1,Bob",
        file_name="users.csv",
    )


# ─────────────────────────────────────────────────────────────────────────────
# GeminiAnalyzer テスト
# ─────────────────────────────────────────────────────────────────────────────

class TestGeminiAnalyzer:

    @patch("src.services.gemini_analyzer.vertexai.init")
    @patch("src.services.gemini_analyzer.GenerativeModel")
    def test_analyze_returns_analysis_result(
        self, mock_model_cls, mock_init, sample_request
    ):
        """正常系: Gemini がレスポンスを返した場合 AnalysisResult が生成される"""
        mock_response = MagicMock()
        mock_response.text = json.dumps(SAMPLE_GEMINI_RESPONSE)
        mock_model = MagicMock()
        mock_model.generate_content.return_value = mock_response
        mock_model_cls.return_value = mock_model

        analyzer = GeminiAnalyzer(project_id="test-project")
        result = analyzer.analyze(sample_request)

        assert isinstance(result, AnalysisResult)
        assert result.risk_level == "LOW"
        assert result.estimated_hours == 0.5
        assert "user_id" in result.root_cause
        assert len(result.migration_steps) == 4
        assert len(result.affected_tables) == 2

    @patch("src.services.gemini_analyzer.vertexai.init")
    @patch("src.services.gemini_analyzer.GenerativeModel")
    def test_analyze_raises_on_invalid_json(self, mock_model_cls, mock_init, sample_request):
        """異常系: Gemini が不正な JSON を返した場合 ValueError が発生する"""
        mock_response = MagicMock()
        mock_response.text = "This is not JSON at all."
        mock_model = MagicMock()
        mock_model.generate_content.return_value = mock_response
        mock_model_cls.return_value = mock_model

        analyzer = GeminiAnalyzer(project_id="test-project")
        with pytest.raises(ValueError, match="JSON パース"):
            analyzer.analyze(sample_request)

    def test_parse_response_strips_code_fence(self):
        """コードフェンス付き JSON でも正常にパースできる"""
        wrapped = f"```json\n{json.dumps(SAMPLE_GEMINI_RESPONSE)}\n```"
        result = GeminiAnalyzer._parse_response(wrapped)
        assert result["risk_level"] == "LOW"

    @pytest.mark.parametrize("risk_level", ["LOW", "MEDIUM", "HIGH", "CRITICAL"])
    def test_analysis_result_risk_levels(self, risk_level):
        """全リスクレベルの AnalysisResult 生成を確認"""
        data = {**SAMPLE_GEMINI_RESPONSE, "risk_level": risk_level}
        result = AnalysisResult.from_dict(data)
        assert result.risk_level == risk_level


# ─────────────────────────────────────────────────────────────────────────────
# CoverageReport テスト (Step 13)
# ─────────────────────────────────────────────────────────────────────────────

class TestCoverageReport:

    def test_passed_threshold_above(self):
        rep = CoverageReport(
            file_path="src/target.py",
            percent_covered=85.0,
            missing_lines=[],
            covered_lines=list(range(1, 86)),
            num_statements=100,
        )
        assert rep.passed_threshold is True

    def test_passed_threshold_below(self):
        rep = CoverageReport(
            file_path="src/target.py",
            percent_covered=65.0,
            missing_lines=list(range(35, 70)),
            covered_lines=list(range(1, 35)),
            num_statements=100,
        )
        assert rep.passed_threshold is False

    def test_passed_threshold_exactly_80(self):
        rep = CoverageReport(
            file_path="src/target.py",
            percent_covered=80.0,
            missing_lines=list(range(81, 101)),
            covered_lines=list(range(1, 81)),
            num_statements=100,
        )
        assert rep.passed_threshold is True


# ─────────────────────────────────────────────────────────────────────────────
# SlackNotifier テスト (Step 5 / Step 9)
# ─────────────────────────────────────────────────────────────────────────────

class TestSlackNotifier:

    @patch("src.services.slack_notifier.SlackNotifier._fetch_secret")
    @patch("src.services.slack_notifier.requests.post")
    def test_notify_sends_blocks(self, mock_post, mock_secret):
        mock_secret.return_value = "https://hooks.slack.com/test"
        mock_post.return_value = MagicMock(status_code=200, raise_for_status=MagicMock())

        notifier = SlackNotifier(project_id="test-project")
        success = notifier.notify_analysis_result(
            file_name="test.csv",
            root_cause="型不整合エラー",
            fixed_code="df['id'] = df['id'].astype(int)",
            migration_steps=["バックアップ", "修正適用"],
            risk_level="LOW",
            estimated_hours=0.5,
        )

        assert success is True
        call_args = mock_post.call_args
        payload = json.loads(call_args.kwargs["data"])
        # Header ブロックが含まれることを確認
        header_block = next(b for b in payload["blocks"] if b["type"] == "header")
        assert "データ連携エラー解析報告" in header_block["text"]["text"]

    @patch("src.services.slack_notifier.SlackNotifier._fetch_secret")
    @patch("src.services.slack_notifier.requests.post")
    def test_notify_with_github_links_adds_buttons(self, mock_post, mock_secret):
        """Step 9: GitHub リンクがあるとき actions ブロック (ボタン) が追加される"""
        mock_secret.return_value = "https://hooks.slack.com/test"
        mock_post.return_value = MagicMock(status_code=200, raise_for_status=MagicMock())

        links = GitHubLinks(
            issue_url="https://github.com/org/repo/issues/42",
            issue_number=42,
            pr_url="https://github.com/org/repo/pull/43",
            pr_number=43,
        )
        notifier = SlackNotifier(project_id="test-project")
        notifier.notify_analysis_result(
            file_name="test.csv",
            root_cause="テストエラー",
            fixed_code="pass",
            migration_steps=[],
            risk_level="MEDIUM",
            estimated_hours=1.0,
            github_links=links,
        )

        call_args = mock_post.call_args
        payload = json.loads(call_args.kwargs["data"])
        action_blocks = [b for b in payload["blocks"] if b["type"] == "actions"]
        assert len(action_blocks) == 1
        elements = action_blocks[0]["elements"]
        urls = [e["url"] for e in elements]
        assert "https://github.com/org/repo/issues/42" in urls
        assert "https://github.com/org/repo/pull/43" in urls

    @patch("src.services.slack_notifier.SlackNotifier._fetch_secret")
    @patch("src.services.slack_notifier.requests.post")
    def test_notify_returns_false_on_http_error(self, mock_post, mock_secret):
        """HTTP エラー時は False を返す（例外を上位に伝播させない）"""
        mock_secret.return_value = "https://hooks.slack.com/test"
        mock_post.side_effect = Exception("Connection error")

        notifier = SlackNotifier(project_id="test-project")
        result = notifier.notify_analysis_result(
            file_name="err.csv",
            root_cause="エラー",
            fixed_code="pass",
            migration_steps=[],
            risk_level="HIGH",
            estimated_hours=2.0,
        )
        assert result is False
