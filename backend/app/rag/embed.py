"""BGE-M3 embeddings (open-source, multilingual — strong on Turkish).

The model (~2.2GB on disk, ~1.8GB resident on CPU once warm) downloads on first
use and runs locally on CPU, which is plenty for this small corpus. Importing this module stays cheap:
the model loads either in a background thread at startup (settings.embed_preload)
or on the first query, exactly once per process.
"""
import logging
import threading
import time

from app.config import settings

logger = logging.getLogger(__name__)

_model_instance = None
# Guards the one-time load: two concurrent first requests must not load two
# copies (~4.6GB) of the model.
_load_lock = threading.Lock()
# The model's fast tokenizer is not thread-safe ("Already borrowed"), and with
# torch pinned to one thread concurrent encodes gain nothing anyway, so encodes run
# one at a time. A query embedding takes well under a second once loaded.
_encode_lock = threading.Lock()


class EmbeddingUnavailable(RuntimeError):
    """The embedding model could not be loaded or failed to encode."""


def _load():
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


def _model():
    global _model_instance
    if _model_instance is not None:
        return _model_instance
    with _load_lock:
        if _model_instance is None:
            started = time.monotonic()
            try:
                _model_instance = _load()
            except Exception as exc:  # noqa: BLE001 -- not cached: the next call retries
                logger.error("embedding model failed to load: %s", type(exc).__name__)
                raise EmbeddingUnavailable("embedding model unavailable") from exc
            logger.info("embedding model loaded in %.1fs", time.monotonic() - started)
    return _model_instance


def is_loaded() -> bool:
    return _model_instance is not None


def preload_in_background() -> threading.Thread:
    """Start loading the model without blocking startup. A request that arrives
    first simply waits on the same lock; a failed load is retried on demand.

    Also runs one warm-up encode: the first encode pays a one-time cost about as
    large as the load itself (weights paged in / moved to the device), which would
    otherwise land on the first user's question."""

    def _run():
        try:
            started = time.monotonic()
            embed_texts(["paket"])
            logger.info("embedding model warm in %.1fs", time.monotonic() - started)
        except EmbeddingUnavailable:
            pass  # already logged
        except Exception as exc:  # noqa: BLE001 -- warm-up is best effort
            logger.warning("embedding warm-up failed: %s", type(exc).__name__)

    thread = threading.Thread(target=_run, name="embed-preload", daemon=True)
    thread.start()
    return thread


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts into dense 1024-dim vectors."""
    model = _model()
    with _encode_lock:
        out = model.encode(texts, batch_size=16, max_length=1024)
    return [vec.tolist() for vec in out["dense_vecs"]]


def embed_query(text: str) -> list[float]:
    """Embed a single query string."""
    try:
        return embed_texts([text])[0]
    except EmbeddingUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("query embedding failed: %s", type(exc).__name__)
        raise EmbeddingUnavailable("query embedding failed") from exc
