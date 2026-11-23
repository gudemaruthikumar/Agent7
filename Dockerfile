FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY agent7 ./agent7
COPY fixtures ./fixtures
RUN pip install --no-cache-dir .

EXPOSE 8000
CMD ["uvicorn", "agent7.api:app", "--host", "0.0.0.0", "--port", "8000"]
