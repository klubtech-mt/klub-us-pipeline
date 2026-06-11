FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir flask pillow requests
RUN chmod +x start.sh
CMD ["sh", "start.sh"]
