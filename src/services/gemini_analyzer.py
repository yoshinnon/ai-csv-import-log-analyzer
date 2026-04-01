"""
src/services/gemini_analyzer.py
Step 2: Vertex AI (Gemini 3 Flash) を使った DB エラー解析サービス
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import vertexai
from vertexai.generative_models import GenerationConfig, GenerativeModel, Part

logger = logging.getLogger(__name__)

_ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "root_cause": {
            "type": "string",
            "description": "エラーの根本原因（日本語で簡潔に）",
        },
        "fixed_code": {
            "type": "string",
            "description": "修正後の完全な Python コード",
        },
        "migration_steps": {
            "type": "array",
            "items": {"type": "string"},
            "description": "段階的な移行手順リスト",
        },
        "impact_scope": {
            "type": "string",
            "description": "影響範囲の説明",
        },
        "risk_level": {
            "type": "string",
            "enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
        },
        "estimated_hours": {
            "type": "number",
            "description": "修正工数の見積もり（時間）",
        },
        "affected_tables": {
            "type": "array",
            "items": {"type": "string"},
            "description": "影響を受けるテーブル名リスト",
        },
        "test_code": {
            "type": "string",
            "description": "修正を検証するための pytest テストコード",
        },
    },
    "required": [
        "root_cause",
        "fixed_code",
        "migration_steps",
        "impact_scope",
        "risk_level",
        "estimated_hours",
        "affected_tables",
        "test_code",
    ],
}

_SYSTEM_PROMPT = """\
あなたは10年以上の経験を持つシニアバックエンドエンジニアです。
CSVデータ連携システムのインポートエラーを解析し、必ず以下の JSON スキーマに準拠した回答のみを返してください。
余分なテキスト・マークダウン記法・コードフェンスは一切不要です。JSON オブジェクトのみを返答してください。

出力 JSON スキーマ:
{schema}

ガイドライン:
- root_cause: 技術的な原因を日本語で簡潔に説明
- fixed_code: 実行可能な Python コード（インポート文を含む完全なコード）
- migration_steps: 番号付きの具体的な手順
- risk_level: LOW（既存データへの影響なし）/ MEDIUM（一部データ変換必要）/ HIGH（データ損失の可能性）/ CRITICAL（即時停止推奨）
- test_code: pytest 形式・アサーション必須・境界値テストを含む完全なテストコード
"""


@dataclass
class AnalysisRequest:
    error_log: str
    source_code: str
    ddl: str
    csv_sample: str = ""
    file_name: str = "unknown.csv"


@dataclass
class AnalysisResult:
    root_cause: str
    fixed_code: str
    migration_steps: list[str]
    impact_scope: str
    risk_level: str
    estimated_hours: float
    affected_tables: list[str]
    test_code: str
    raw_response: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AnalysisResult":
        return cls(
            root_cause=data["root_cause"],
            fixed_code=data["fixed_code"],
            migration_steps=data["migration_steps"],
            impact_scope=data["impact_scope"],
            risk_level=data["risk_level"],
            estimated_hours=float(data["estimated_hours"]),
            affected_tables=data.get("affected_tables", []),
            test_code=data.get("test_code", ""),
            raw_response=data,
        )


class GeminiAnalyzer:
    """Vertex AI Gemini 3 Flash を使用したエラー解析エンジン"""

    MODEL_ID = "gemini-3-flash"  # Gemini 3 Flash

    def __init__(self, project_id: str, region: str = "asia-northeast1") -> None:
        vertexai.init(project=project_id, location=region)
        self._model = GenerativeModel(
            model_name=self.MODEL_ID,
            system_instruction=_SYSTEM_PROMPT.format(
                schema=json.dumps(_ANALYSIS_SCHEMA, ensure_ascii=False, indent=2)
            ),
        )
        self._gen_config = GenerationConfig(
            temperature=0.2,       # 解析タスクは低温で安定した出力
            max_output_tokens=4096,
            response_mime_type="application/json",
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(self, req: AnalysisRequest) -> AnalysisResult:
        """エラーログ・ソースコード・DDL を相関分析して修正案を返す"""
        prompt = self._build_analysis_prompt(req)
        logger.info("Gemini 解析開始: file=%s", req.file_name)

        response = self._model.generate_content(
            prompt,
            generation_config=self._gen_config,
        )

        raw = self._parse_response(response.text)
        result = AnalysisResult.from_dict(raw)
        logger.info(
            "Gemini 解析完了: risk=%s hours=%.1f",
            result.risk_level,
            result.estimated_hours,
        )
        return result

    def generate_additional_tests(
        self,
        existing_test_code: str,
        source_code: str,
        missing_lines: list[int],
        current_coverage: float,
    ) -> str:
        """
        Step 14: カバレッジが 80% 未満の場合に追加テストを生成する
        既存テストを破壊せず Append する形式で返却
        """
        prompt = f"""\
現在のテストカバレッジは {current_coverage:.1f}% です（目標: 80%）。
未通過の行番号: {missing_lines}

以下の制約に従い、追加のテストケースのみを生成してください:
1. 既存テストコードは変更しない（新しいテスト関数のみ追加）
2. 境界値テスト（boundary value testing）を必ず含む
3. 各テスト関数に具体的な assert 文を記述する
4. 未通過行 {missing_lines} が実行されるように設計する
5. pytest 形式で記述する

## 対象ソースコード
```python
{source_code}
```

## 既存テストコード（参考）
```python
{existing_test_code}
```

追加テストコードのみを返してください（既存テストの再出力は不要）。
コードフェンスなし、Pythonコードのみ出力してください。
"""
        response = self._model.generate_content(
            prompt,
            generation_config=GenerationConfig(temperature=0.1, max_output_tokens=2048),
        )
        return response.text.strip()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_analysis_prompt(self, req: AnalysisRequest) -> str:
        csv_section = (
            f"\n## CSV サンプルデータ（先頭5行）\n```\n{req.csv_sample}\n```"
            if req.csv_sample
            else ""
        )
        return f"""\
以下の情報を相関分析し、インポートエラーの根本原因と修正案を JSON で返してください。

## エラーログ
```
{req.error_log}
```

## 対象ソースコード
```python
{req.source_code}
```

## DB テーブル定義（DDL）
```sql
{req.ddl}
```
{csv_section}

ファイル名: {req.file_name}
"""

    @staticmethod
    def _parse_response(text: str) -> dict[str, Any]:
        """レスポンスから JSON を安全に抽出する"""
        # コードフェンスが含まれる場合は除去
        cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", text).strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as exc:
            logger.error("JSON パース失敗: %s\nraw=%s", exc, text[:500])
            raise ValueError(f"Gemini レスポンスの JSON パースに失敗: {exc}") from exc
