import torch
from fastapi import FastAPI
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

_INSTRUCTION = "Represent this cybersecurity article for finding related articles: "
_MODEL_NAME = "BAAI/bge-large-en-v1.5"
_device = "cuda" if torch.cuda.is_available() else "cpu"
_model = SentenceTransformer(_MODEL_NAME, device=_device)

app = FastAPI()


class EmbedRequest(BaseModel):
    text: str


class BatchEmbedRequest(BaseModel):
    texts: list[str]


@app.get("/health")
def health():
    return {"status": "ok", "device": _device, "model": _MODEL_NAME}


@app.post("/embed")
def embed(req: EmbedRequest):
    vec = _model.encode(_INSTRUCTION + req.text, normalize_embeddings=True)
    return {"embedding": vec.tolist()}


@app.post("/embed/batch")
def embed_batch(req: BatchEmbedRequest):
    texts = [_INSTRUCTION + t for t in req.texts]
    vecs = _model.encode(texts, normalize_embeddings=True, batch_size=32)
    return {"embeddings": [v.tolist() for v in vecs]}
