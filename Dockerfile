################################################################################
# Dockerfile – AI CSV Analyzer (Cloud Run)
################################################################################
FROM python:3.12-slim AS base

WORKDIR /app

# システム依存（pytest-cov のテスト実行に必要）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 依存ライブラリを先にインストール（キャッシュ活用）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# アプリコードをコピー
COPY src/ ./src/
COPY tests/ ./tests/

# Cloud Run は PORT 環境変数を使用
ENV PORT=8080

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
