FROM python:3.12-slim
WORKDIR /opt/pdsia
ENV PYTHONUNBUFFERED=1 PDSIA_DB=/var/lib/pdsia/pdsia.db
COPY pyproject.toml README.md ./
COPY src ./src
COPY data/fixtures ./data/fixtures
COPY data/simulated ./data/simulated
COPY sql ./sql
COPY web ./web
RUN pip install --no-cache-dir -e . 'fastapi>=0.110' 'uvicorn>=0.29'
RUN useradd -u 10001 -m agent && mkdir -p /var/lib/pdsia && chown agent:agent /var/lib/pdsia
USER agent
EXPOSE 8000
CMD ["uvicorn", "pdsia_plus.api:app", "--host", "0.0.0.0", "--port", "8000"]
