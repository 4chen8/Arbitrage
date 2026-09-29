FROM python:3.11-slim
WORKDIR /app
COPY requirements*.txt ./
RUN pip install --no-cache-dir -r requirements-full.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "arbitrage.server:app", "--host", "0.0.0.0", "--port", "8000"]
