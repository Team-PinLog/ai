# FastAPI AI 서버 이미지. Python은 3.12 고정(팀 환경 통일, .python-version과 일치).
FROM python:3.12-slim

WORKDIR /app

# 런타임 의존성만 lock으로 설치(dev·테스트 제외).
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

# 애플리케이션 코드와 Preset 시드.
COPY app ./app
COPY data ./data

# 비루트 실행.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser /app
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
