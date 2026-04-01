"""
src/services/test_runner.py
Step 11 / Step 13 / Step 14: テスト実行エンジン + pytest-cov カバレッジ計測 + 再帰的補完
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.services.gemini_analyzer import GeminiAnalyzer

logger = logging.getLogger(__name__)

MAX_COVERAGE_RETRIES = 5        # Step 14: 最大反復回数
COVERAGE_THRESHOLD = 80.0       # 目標カバレッジ (%)


@dataclass
class CoverageReport:
    """Step 13: ファイルごとのカバレッジ情報"""
    file_path: str
    percent_covered: float
    missing_lines: list[int]
    covered_lines: list[int]
    num_statements: int

    @property
    def passed_threshold(self) -> bool:
        return self.percent_covered >= COVERAGE_THRESHOLD


@dataclass
class TestRunResult:
    """テスト実行結果の集約"""
    passed: bool
    stdout: str
    stderr: str
    exit_code: int
    coverage_reports: list[CoverageReport] = field(default_factory=list)
    test_code_used: str = ""
    iterations: int = 0

    def to_summary_text(self) -> str:
        lines = [
            f"{'✅ PASSED' if self.passed else '❌ FAILED'}  (反復回数: {self.iterations})",
            "",
            self.stdout[-2000:] if len(self.stdout) > 2000 else self.stdout,
        ]
        return "\n".join(lines)

    def to_coverage_table(self) -> str:
        """Step 12: PR に貼る Markdown テーブル形式のカバレッジサマリー"""
        if not self.coverage_reports:
            return ""
        rows = ["| ファイル | カバレッジ | 未通過行 | 状態 |",
                "|----------|-----------|---------|------|"]
        for rep in self.coverage_reports:
            status = "✅" if rep.passed_threshold else "⚠️"
            missing = ", ".join(str(l) for l in rep.missing_lines[:10])
            if len(rep.missing_lines) > 10:
                missing += " ..."
            rows.append(
                f"| `{rep.file_path}` | {rep.percent_covered:.1f}% | {missing or '—'} | {status} |"
            )
        return "\n".join(rows)


class TestRunner:
    """
    pytest + pytest-cov を使ったテスト実行エンジン
    カバレッジが閾値未満の場合、Gemini に追加テストを要求して反復する
    """

    def __init__(self, gemini_analyzer: "GeminiAnalyzer") -> None:
        self._analyzer = gemini_analyzer

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_with_coverage_loop(
        self,
        *,
        source_code: str,
        test_code: str,
        source_file_name: str = "target.py",
        test_file_name: str = "test_target.py",
    ) -> TestRunResult:
        """
        Step 11 + Step 14: テスト実行 → カバレッジ計測 → 不足分を Gemini で補完 → 反復
        最大 MAX_COVERAGE_RETRIES 回まで反復する
        """
        with tempfile.TemporaryDirectory(prefix="ai_test_") as tmpdir:
            src_path = Path(tmpdir) / source_file_name
            test_path = Path(tmpdir) / test_file_name

            src_path.write_text(source_code, encoding="utf-8")
            accumulated_test = test_code

            for iteration in range(1, MAX_COVERAGE_RETRIES + 1):
                logger.info("テスト実行 イテレーション %d/%d", iteration, MAX_COVERAGE_RETRIES)
                test_path.write_text(accumulated_test, encoding="utf-8")

                run_result = self._execute_pytest(
                    tmpdir=tmpdir,
                    src_file=source_file_name,
                    test_file=test_file_name,
                )

                # カバレッジ JSON を解析
                cov_reports = self._parse_coverage_json(tmpdir, source_file_name)

                # カバレッジが十分か判定
                all_passed = all(r.passed_threshold for r in cov_reports)
                overall_cov = (
                    sum(r.percent_covered for r in cov_reports) / len(cov_reports)
                    if cov_reports else 0.0
                )

                logger.info(
                    "カバレッジ: %.1f%% (閾値: %.1f%%) – %s",
                    overall_cov,
                    COVERAGE_THRESHOLD,
                    "✅ 達成" if all_passed else "🔄 未達",
                )

                if all_passed or not run_result.passed:
                    # 目標達成 or テスト自体が失敗した場合は終了
                    return TestRunResult(
                        passed=run_result.passed and all_passed,
                        stdout=run_result["stdout"],
                        stderr=run_result["stderr"],
                        exit_code=run_result["exit_code"],
                        coverage_reports=cov_reports,
                        test_code_used=accumulated_test,
                        iterations=iteration,
                    )

                if iteration < MAX_COVERAGE_RETRIES:
                    # Step 14: 追加テストを Gemini に依頼
                    all_missing = []
                    for rep in cov_reports:
                        all_missing.extend(rep.missing_lines)

                    logger.info("Gemini に追加テストを要求: missing_lines=%s", all_missing[:20])
                    additional = self._analyzer.generate_additional_tests(
                        existing_test_code=accumulated_test,
                        source_code=source_code,
                        missing_lines=sorted(set(all_missing)),
                        current_coverage=overall_cov,
                    )
                    # Append（既存テストは破壊しない）
                    accumulated_test = accumulated_test.rstrip() + "\n\n\n" + additional
                else:
                    logger.warning(
                        "最大試行回数 %d に達しました。最終カバレッジ: %.1f%%",
                        MAX_COVERAGE_RETRIES,
                        overall_cov,
                    )

            # フォールスルー（ループ正常終了しなかった場合）
            final_run = self._execute_pytest(tmpdir=tmpdir, src_file=source_file_name, test_file=test_file_name)
            cov_reports = self._parse_coverage_json(tmpdir, source_file_name)
            return TestRunResult(
                passed=final_run["exit_code"] == 0,
                stdout=final_run["stdout"],
                stderr=final_run["stderr"],
                exit_code=final_run["exit_code"],
                coverage_reports=cov_reports,
                test_code_used=accumulated_test,
                iterations=MAX_COVERAGE_RETRIES,
            )

    # ------------------------------------------------------------------
    # Private: pytest 実行 (Step 11)
    # ------------------------------------------------------------------

    @staticmethod
    def _execute_pytest(
        *, tmpdir: str, src_file: str, test_file: str
    ) -> dict:
        """subprocess で pytest --cov を実行する"""
        cov_source = src_file.replace(".py", "")
        cmd = [
            "python", "-m", "pytest",
            test_file,
            f"--cov={cov_source}",
            "--cov-report=json:coverage.json",
            "--cov-report=term-missing",
            "-v",
            "--tb=short",
        ]
        result = subprocess.run(
            cmd,
            cwd=tmpdir,
            capture_output=True,
            text=True,
            timeout=120,
            env={**os.environ, "PYTHONPATH": tmpdir},
        )
        return {
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "passed": result.returncode == 0,
        }

    # ------------------------------------------------------------------
    # Private: カバレッジ JSON パース (Step 13)
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_coverage_json(tmpdir: str, source_file_name: str) -> list[CoverageReport]:
        """
        pytest-cov が生成する coverage.json を解析して CoverageReport のリストを返す
        """
        cov_path = Path(tmpdir) / "coverage.json"
        if not cov_path.exists():
            logger.warning("coverage.json が見つかりません")
            return []

        with cov_path.open(encoding="utf-8") as f:
            data = json.load(f)

        reports: list[CoverageReport] = []
        for file_path, file_data in data.get("files", {}).items():
            # 対象ファイルのみ
            if source_file_name.replace(".py", "") not in file_path:
                continue
            summary = file_data.get("summary", {})
            executed = set(file_data.get("executed_lines", []))
            missing = file_data.get("missing_lines", [])

            reports.append(
                CoverageReport(
                    file_path=file_path,
                    percent_covered=summary.get("percent_covered", 0.0),
                    missing_lines=sorted(missing),
                    covered_lines=sorted(executed),
                    num_statements=summary.get("num_statements", 0),
                )
            )
        return reports
