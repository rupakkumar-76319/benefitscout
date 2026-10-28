FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1 BS_DATA_DIR=/data
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY benefitscout ./benefitscout
COPY web ./web
COPY scripts ./scripts
RUN mkdir -p /data && chmod 777 /data
# Hugging Face Spaces and most hosts inject PORT; default 7860 for Spaces.
ENV PORT=7860
EXPOSE 7860
CMD ["sh", "-c", "uvicorn benefitscout.server:app --host 0.0.0.0 --port ${PORT}"]
