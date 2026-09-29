FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Keeps scraping on the schedule in config.yaml. Mount ./data to keep results:
#   docker run -d -v "$(pwd)/data:/app/data" auto-web-scraper
CMD ["python", "main.py", "schedule"]
