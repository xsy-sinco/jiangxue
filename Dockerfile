FROM python:3.11-slim

# 时区设上海，方便看日志
ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    WSGI_AUTOINIT=1

WORKDIR /app

# 先复制 requirements 让 pip 层可缓存
# 默认用阿里云 PyPI 镜像加速（国内构建快 10 倍）。
# 海外环境构建可覆盖：docker build --build-arg PIP_INDEX_URL=https://pypi.org/simple .
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG PIP_TRUSTED_HOST=mirrors.aliyun.com
COPY requirements.txt .
RUN pip install --no-cache-dir \
    -i ${PIP_INDEX_URL} --trusted-host ${PIP_TRUSTED_HOST} \
    -r requirements.txt

# 复制源码
COPY src/ ./src/
COPY web/ ./web/

# 数据目录（cache + 聚合 JSON），与外部 volume 挂载
RUN mkdir -p /app/data/matches

EXPOSE 8000

# 1 个 worker + 4 个 thread：因为我们用了进程内状态（_state、threading.Thread 后台刷新），
# 多 worker 会各持一份不一致的状态。多 thread 足够支撑几百并发查询。
CMD ["gunicorn", "-w", "1", "--threads", "4", "-b", "0.0.0.0:8000", \
     "--access-logfile", "-", "--error-logfile", "-", \
     "--timeout", "120", \
     "web.app:app"]
