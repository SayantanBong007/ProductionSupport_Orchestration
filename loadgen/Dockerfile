FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY loadgen.py .
RUN useradd -m -u 1000 appuser
USER appuser
EXPOSE 9100
CMD ["python", "loadgen.py"]
