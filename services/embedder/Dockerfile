FROM pytorch/pytorch:2.3.0-cuda12.1-cudnn8-runtime

WORKDIR /app
ENV TRANSFORMERS_CACHE=/model-cache

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
