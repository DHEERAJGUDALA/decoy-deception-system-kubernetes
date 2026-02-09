FROM python:3.11-alpine AS builder
WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

FROM python:3.11-alpine
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY simulate_attacks.py .
COPY demo-scenario.sh .
RUN chmod +x demo-scenario.sh
ENV PATH="/opt/venv/bin:$PATH"
RUN adduser -D -u 1000 appuser
USER appuser
ENTRYPOINT ["python", "simulate_attacks.py"]
CMD ["--attack-type", "all"]
