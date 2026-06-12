FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PHOTO_WALL_HOST=0.0.0.0 \
    PHOTO_WALL_PORT=8765 \
    PHOTO_WALL_DATA_DIR=/app/data

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data/uploads /app/data/photos

EXPOSE 8765

CMD ["python", "server.py"]
