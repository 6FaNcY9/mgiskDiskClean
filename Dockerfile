FROM python:3.11-slim-bullseye
WORKDIR /app

COPY pyproject.toml ./
COPY src/ ./src/

RUN apt-get update && apt-get install -y --no-install-recommends rsync openssh-client && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir .

VOLUME ["/data"]
EXPOSE 8080

ENV MRIJA_API_KEY=""

CMD ["python", "-m", "mrija_client", \
     "--db", "/data/mail_index.sqlite", \
     "--bind", "0.0.0.0", \
     "--port", "8080", \
     "--no-tui", \
     "--mode", "admin"]
