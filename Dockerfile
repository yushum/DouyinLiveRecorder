FROM python:3.11-slim

ARG VERSION=v4.0.7.6

LABEL org.opencontainers.image.title="DouyinLiveRecorder" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.source="https://github.com/yushum/DouyinLiveRecorder" \
      org.opencontainers.image.licenses="MIT"

WORKDIR /app

COPY . /app

RUN apt-get update && \
    apt-get install -y curl gnupg && \
    curl -sL https://deb.nodesource.com/setup_20.x  | bash - && \
    apt-get install -y nodejs

RUN pip install --no-cache-dir -r requirements.txt

RUN apt-get update && \
    apt-get install -y ffmpeg tzdata && \
    ln -fs /usr/share/zoneinfo/Asia/Shanghai /etc/localtime && \
    dpkg-reconfigure -f noninteractive tzdata

CMD ["python", "main.py"]
