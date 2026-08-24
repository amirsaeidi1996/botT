FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && python -m playwright install --with-deps chromium
COPY . .
RUN if [ ! -f .env ]; then cp .env.example .env; fi
ENV PORT=8080
EXPOSE 8080
CMD ["python", "app.py"]
