"""BGE-M3 embeddings (open-source, multilingual — strong on Turkish).

The model (~2GB) downloads on first use and runs locally on CPU, which is
plenty for this small corpus. Loaded lazily and cached so importing this
module stays cheap.
"""
from functools import lru_cache

from app.config import settings


@lru_cache(maxsize=1)
def _model():
    # Imported lazily so the web app / tests that don't embed don't pay the
    # torch import + model-load cost.
    import torch
    from FlagEmbedding import BGEM3FlagModel

    # Pin torch to a single thread. Under an async server run without --reload
    # (i.e. the way it runs in production), OpenMP thread oversubscription between
    # uvicorn's event loop and torch can corrupt the forward pass into an all-NaN
    # embedding, which pgvector then rejects. Single-threaded is fast enough for
    # this small corpus and makes results deterministic across run modes.
    torch.set_num_threads(1)

    return BGEM3FlagModel(settings.embed_model, use_fp16=False)


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts into dense 1024-dim vectors."""
    out = _model().encode(texts, batch_size=16, max_length=1024)
    return [vec.tolist() for vec in out["dense_vecs"]]


def embed_query(text: str) -> list[float]:
    """Embed a single query string."""
    return embed_texts([text])[0]
