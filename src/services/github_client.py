"""
src/services/github_client.py
Step 8: PyGitHub を使った Issue / Branch / PR 自動作成
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from github import Github, GithubException
from google.cloud import secretmanager

logger = logging.getLogger(__name__)


@dataclass
class GitHubArtifacts:
    issue_url: str
    issue_number: int
    pr_url: str
    pr_number: int
    branch_name: str


class GitHubClient:
    """
    Gemini 解析結果を元に GitHub の Issue / Branch / PR を自動作成する
    """

    def __init__(
        self,
        project_id: str,
        repo_name: str,
        secret_id: str = "github-pat",
    ) -> None:
        pat = self._fetch_secret(project_id, secret_id)
        self._gh = Github(pat)
        self._repo = self._gh.get_repo(repo_name)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_fix_artifacts(
        self,
        *,
        error_summary: str,
        root_cause: str,
        fixed_code: str,
        migration_steps: list[str],
        risk_level: str,
        estimated_hours: float,
        target_file_path: str,
        test_code: str = "",
        test_results: str = "",
        coverage_table: str = "",
    ) -> GitHubArtifacts:
        """
        Step 8: 以下を順番に実行する
        1. GitHub Issue を作成
        2. fix/ai-analysis-[issue_id] ブランチを作成
        3. 修正コードでファイルを更新
        4. PR を作成（closes #issue_id 付き）
        """
        # 1. Issue 作成
        issue = self._create_issue(
            root_cause=root_cause,
            error_summary=error_summary,
            migration_steps=migration_steps,
            risk_level=risk_level,
            estimated_hours=estimated_hours,
        )
        logger.info("Issue 作成完了: #%d %s", issue.number, issue.html_url)

        # 2. ブランチ作成
        branch_name = f"fix/ai-analysis-{issue.number}"
        self._create_branch(branch_name)
        logger.info("ブランチ作成完了: %s", branch_name)

        # 3. ファイル更新
        self._update_file(
            branch_name=branch_name,
            file_path=target_file_path,
            new_content=fixed_code,
            commit_message=f"fix: AI解析による自動修正 (closes #{issue.number})",
        )
        logger.info("ファイル更新完了: %s", target_file_path)

        # 4. PR 作成
        pr = self._create_pull_request(
            branch_name=branch_name,
            issue_number=issue.number,
            root_cause=root_cause,
            migration_steps=migration_steps,
            risk_level=risk_level,
            estimated_hours=estimated_hours,
            test_code=test_code,
            test_results=test_results,
            coverage_table=coverage_table,
        )
        logger.info("PR 作成完了: #%d %s", pr.number, pr.html_url)

        return GitHubArtifacts(
            issue_url=issue.html_url,
            issue_number=issue.number,
            pr_url=pr.html_url,
            pr_number=pr.number,
            branch_name=branch_name,
        )

    # ------------------------------------------------------------------
    # Private: Issue
    # ------------------------------------------------------------------

    def _create_issue(
        self,
        *,
        root_cause: str,
        error_summary: str,
        migration_steps: list[str],
        risk_level: str,
        estimated_hours: float,
    ):
        steps_md = "\n".join(
            f"{i + 1}. {step}" for i, step in enumerate(migration_steps)
        )
        body = f"""\
## 🤖 AI自律解析レポート

> このIssueはGemini 3 Flash (Vertex AI) が自動生成しました。

### 根本原因
{root_cause}

### エラー概要
{error_summary}

### 移行手順
{steps_md}

### メタデータ
| 項目 | 値 |
|------|-----|
| リスクレベル | {risk_level} |
| 工数見積 | {estimated_hours}h |
| 解析エンジン | Gemini 3 Flash |
"""
        return self._repo.create_issue(
            title=f"[AI解析] {root_cause[:80]}",
            body=body,
            labels=self._ensure_labels(["ai-generated", f"risk-{risk_level.lower()}"]),
        )

    # ------------------------------------------------------------------
    # Private: Branch
    # ------------------------------------------------------------------

    def _create_branch(self, branch_name: str) -> None:
        default_branch = self._repo.get_branch(self._repo.default_branch)
        try:
            self._repo.create_git_ref(
                ref=f"refs/heads/{branch_name}",
                sha=default_branch.commit.sha,
            )
        except GithubException as exc:
            if exc.status == 422:
                logger.warning("ブランチ %s は既に存在します", branch_name)
            else:
                raise

    # ------------------------------------------------------------------
    # Private: File Update
    # ------------------------------------------------------------------

    def _update_file(
        self,
        *,
        branch_name: str,
        file_path: str,
        new_content: str,
        commit_message: str,
    ) -> None:
        try:
            existing = self._repo.get_contents(file_path, ref=branch_name)
            self._repo.update_file(
                path=file_path,
                message=commit_message,
                content=new_content,
                sha=existing.sha,
                branch=branch_name,
            )
        except GithubException as exc:
            if exc.status == 404:
                self._repo.create_file(
                    path=file_path,
                    message=commit_message,
                    content=new_content,
                    branch=branch_name,
                )
            else:
                raise

    # ------------------------------------------------------------------
    # Private: Pull Request (Step 12: カバレッジサマリー付き)
    # ------------------------------------------------------------------

    def _create_pull_request(
        self,
        *,
        branch_name: str,
        issue_number: int,
        root_cause: str,
        migration_steps: list[str],
        risk_level: str,
        estimated_hours: float,
        test_code: str,
        test_results: str,
        coverage_table: str,
    ):
        steps_md = "\n".join(
            f"- [x] {step}" for step in migration_steps
        )

        test_section = ""
        if test_results:
            test_section = f"""
## ✅ AI 実行テスト結果（Step 12）

```
{test_results}
```
"""
        coverage_section = ""
        if coverage_table:
            coverage_section = f"""
## 📊 カバレッジサマリー（pytest-cov）

{coverage_table}
"""

        test_code_section = ""
        if test_code:
            test_code_section = f"""
## 🧪 AI 生成テストコード

```python
{test_code}
```
"""

        body = f"""\
## 🤖 AI自律修正 PR

closes #{issue_number}

### 変更概要
{root_cause}

### 実施内容
{steps_md}

### メタデータ
| 項目 | 値 |
|------|-----|
| リスクレベル | {risk_level} |
| 工数見積 | {estimated_hours}h |
| 解析エンジン | Gemini 3 Flash (Vertex AI) |
{test_section}{coverage_section}{test_code_section}

---
> ⚠️ このPRはAIが自動生成しました。マージ前に人間によるレビューを必ず実施してください。
"""
        return self._repo.create_pull(
            title=f"fix: [AI自律修正] {root_cause[:60]} (closes #{issue_number})",
            body=body,
            head=branch_name,
            base=self._repo.default_branch,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _ensure_labels(self, label_names: list[str]) -> list:
        """存在しないラベルは作成してから返す"""
        existing = {lb.name: lb for lb in self._repo.get_labels()}
        result = []
        color_map = {
            "ai-generated": "0075ca",
            "risk-low": "0e8a16",
            "risk-medium": "e4e669",
            "risk-high": "e11d48",
            "risk-critical": "b91c1c",
        }
        for name in label_names:
            if name in existing:
                result.append(existing[name])
            else:
                try:
                    lb = self._repo.create_label(
                        name=name, color=color_map.get(name, "ededed")
                    )
                    result.append(lb)
                except GithubException:
                    pass
        return result

    @staticmethod
    def _fetch_secret(project_id: str, secret_id: str) -> str:
        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("utf-8").strip()
