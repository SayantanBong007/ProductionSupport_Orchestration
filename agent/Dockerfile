FROM python:3.11-slim
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download ChromaDB's ONNX embedding model at build time so the container
# never blocks on a network download during a live investigation.
RUN python -c "\
import chromadb; \
c = chromadb.EphemeralClient(); \
col = c.get_or_create_collection('warmup'); \
col.add(ids=['w1'], documents=['warm up the embedding model']); \
col.query(query_texts=['warm up'], n_results=1)"

COPY pyproject.toml .
COPY src ./src
RUN pip install --no-cache-dir --no-deps .

EXPOSE 8080
CMD ["uvicorn", "sre_agent.api.app:app", "--host", "0.0.0.0", "--port", "8080"]
