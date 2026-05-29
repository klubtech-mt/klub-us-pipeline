FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir requests
CMD ["python3", "20250529_us_pipeline_v27.py", "demo", "us_leads_pipeline.csv"]
