FROM python:3.9-slim

RUN apt-get update && apt-get install -y \
    avahi-utils \
    libavahi-client3 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install docker

WORKDIR /app
COPY bridge.py .

ENV AVAHI_COMPAT_NOWARN=1

CMD ["python", "bridge.py"]
