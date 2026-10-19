# Playwright needs deps + browser binaries.
FROM mcr.microsoft.com/playwright/python:v1.49.1

WORKDIR /app

# Deps (playwright base image already has chromium + system deps).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8099

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8099"]