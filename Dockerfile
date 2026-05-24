FROM python:3.12-slim

# 安装系统依赖（Playwright 需要）
RUN apt-get update && \
    apt-get install -y \
        wget \
        curl \
        unzip \
        gnupg \
        libnss3 \
        libnspr4 \
        libatk1.0-0 \
        libatk-bridge2.0-0 \
        libcups2 \
        libdrm2 \
        libdbus-1-3 \
        libxcb1 \
        libxkbcommon0 \
        libx11-6 \
        libxcomposite1 \
        libxdamage1 \
        libxext6 \
        libxfixes3 \
        libxrandr2 \
        libgbm1 \
        libpango-1.0-0 \
        libcairo2 \
        libasound2 \
        libatspi2.0-0 \
        libxshmfence1 \
        libxrandr2 \
        fonts-liberation \
        xdg-utils \
        libxss1 \
        libegl1 \
        libgl1-mesa-glx \
        procps \
        && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 复制依赖文件并安装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    playwright install chromium --with-deps

# 复制脚本文件
COPY chinamobile.py .
COPY chinamobile_capture.py .
COPY chinamobile_config.example.json ./chinamobile_config.example.json

# 创建数据目录
RUN mkdir -p /app/chinamobile_data

# 配置文件挂载点
VOLUME ["/app/chinamobile_data", "/app/chinamobile_config.json"]

# 默认命令：运行查询
CMD ["python", "chinamobile.py", "--query"]
