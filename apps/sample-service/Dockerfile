# Container image for the sample service.
#
# NOTE: this Dockerfile is part of the deliverable but has NOT been built or run in
# the development environment used to produce this project, which has no container
# runtime. See docs/security.md and docs/adr/0001.
FROM python:3.12-slim AS base

# Run as an unprivileged user. The image contains no shell-accessible secrets.
RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

COPY pyproject.toml ./
COPY sample_service ./sample_service
RUN pip install --no-cache-dir .

USER app
EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health').status==200 else 1)"

CMD ["python", "-m", "uvicorn", "sample_service.main:app", \
     "--host", "0.0.0.0", "--port", "8080", "--log-level", "warning"]
