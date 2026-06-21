FROM python:3.12

RUN pip install --no-cache-dir \
    surya-ocr \
    fastapi \
    uvicorn[standard] \
    httpx

WORKDIR /app

COPY app.py .

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]