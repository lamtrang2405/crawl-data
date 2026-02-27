FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy
WORKDIR /app
COPY appmagic_crawler_web.py appmagic_top_charts_requirements.txt ./
RUN pip install --no-cache-dir -r appmagic_top_charts_requirements.txt gunicorn
EXPOSE 5000
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "2", "--timeout", "120", "appmagic_crawler_web:app"]
