# OPERA AI — Production Dockerfile
FROM python:3.14-slim

WORKDIR /app

# 시스템 의존성
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-kor \
    tesseract-ocr-eng \
    libgl1-mesa-glx \
    && rm -rf /var/lib/apt/lists/*

# Python 패키지
COPY agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스코드
COPY agent/ /app/agent/
COPY index.html /app/index.html
COPY pages/ /app/pages/
COPY docs/ /app/docs/

# 데이터 디렉토리
RUN mkdir -p /app/agent/data

EXPOSE 5000

CMD ["python3", "/app/agent/server.py", "--port", "5000", "--host", "0.0.0.0"]
