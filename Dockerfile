# Pinned to Debian 12 because the Microsoft ODBC repository below is Debian-12 specific.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ACCEPT_EULA=Y

# Microsoft ODBC Driver 18 for SQL Server (needed by pyodbc)
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl gnupg2 ca-certificates unixodbc-dev \
    && curl -sSL https://packages.microsoft.com/keys/microsoft.asc \
        | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && curl -sSL https://packages.microsoft.com/config/debian/12/prod.list \
        -o /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends msodbcsql18 \
    && apt-get purge -y --auto-remove curl gnupg2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY config ./config
RUN pip install --no-cache-dir ".[mssql]"

# Run as a normal user; /data is where output and logs go (mount a volume there).
RUN useradd --create-home recon && mkdir -p /data && chown recon /data
USER recon
WORKDIR /data

ENTRYPOINT ["pricing-reconciler", "--config-dir", "/app/config"]
CMD ["run"]
