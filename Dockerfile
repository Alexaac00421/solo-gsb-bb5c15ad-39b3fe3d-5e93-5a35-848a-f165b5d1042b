FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv/app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000

# 服务启动时会自动等待数据库就绪并初始化表结构（见 app/main.py 的 lifespan）。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
