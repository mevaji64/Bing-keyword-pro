FROM python:3.12-slim
WORKDIR /app
COPY . /app
ENV BKP_HOST=0.0.0.0 BKP_DATA_DIR=/app/data PORT=8765
EXPOSE 8765
VOLUME ["/app/data"]
CMD ["python", "app.py", "--no-browser"]
