"""Offline reliability/security tests: provider timeouts and retries, the
/chat quota and session lifecycle, input validation, client IP, JWT, embedding
lifecycle. No database, model or network access (a local socket server stands in
for a slow provider)."""
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient
from openai import OpenAI

import app.auth as auth_mod
import app.main as main_mod
import app.rag.embed as embed_mod
import app.rag.generate as generate_mod
import app.rag.retrieve as retrieve_mod
from app.auth import AuthUser, get_current_user
from app.client_ip import resolve_client_ip, trusted_networks, _parse_trusted
from app.config import settings
from app.db import get_session
from app.ingest.load import build_catalog
from app.models import Package
from app.rag.embed import EmbeddingUnavailable
from app.rag.generate import ProviderUnavailable, generate_answer

_OK_BODY = {
    "id": "x", "object": "chat.completion", "created": 0, "model": "m",
    "choices": [{"index": 0, "finish_reason": "stop",
                 "message": {"role": "assistant", "content": "**Öneri:** bu paket [S1]"}}],
}


def _package() -> Package:
    rec = build_catalog()[0][0]
    cols = {c.name for c in Package.__table__.columns}
    return Package(id=uuid.uuid4(), **{k: v for k, v in rec.items() if k in cols})


def _sdk_client(handler=None, base_url="http://provider.test/v1", timeout=None) -> OpenAI:
    """The production client configuration, optionally over a mock transport."""
    http_client = httpx.Client(transport=httpx.MockTransport(handler)) if handler else None
    return OpenAI(
        base_url=base_url,
        api_key="test-key",
        timeout=timeout or httpx.Timeout(settings.provider_timeout_seconds, connect=settings.provider_connect_timeout_seconds),
        max_retries=settings.provider_max_retries,
        http_client=http_client,
    )


# ---- 1/2. provider timeouts, retries, classification ----

class _SlowProvider(BaseHTTPRequestHandler):
    delay = 3.0
    hits = 0

    def do_POST(self):
        type(self).hits += 1
        time.sleep(self.delay)
        try:
            self.send_response(200)
            self.end_headers()
        except OSError:
            pass

    def log_message(self, *args):
        pass


def test_provider_timeout_is_bounded_and_retried_once(monkeypatch):
    """A provider that never answers in time: two attempts (1 + 1 retry), each cut at
    the read timeout, then ProviderUnavailable('timeout') -- not a 10-minute hang."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SlowProvider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _SlowProvider.hits = 0
    try:
        client = _sdk_client(base_url=f"http://127.0.0.1:{server.server_port}/v1",
                             timeout=httpx.Timeout(0.5, connect=0.5))
        monkeypatch.setattr(generate_mod, "_client", lambda: client)
        started = time.monotonic()
        with pytest.raises(ProviderUnavailable) as err:
            generate_answer("soru", [_package()])
        elapsed = time.monotonic() - started
    finally:
        server.shutdown()
    assert err.value.kind == "timeout"
    assert _SlowProvider.hits == settings.provider_max_retries + 1 == 2
    assert elapsed < 3.0  # 2 x 0.5 s + SDK backoff (<1 s)


def test_production_client_has_explicit_bounds():
    generate_mod._client.cache_clear()
    try:
        client = generate_mod._client()
        assert client.max_retries == settings.provider_max_retries == 1
        assert client.timeout.read == settings.provider_timeout_seconds == 30.0
        assert client.timeout.connect == settings.provider_connect_timeout_seconds == 5.0
    finally:
        generate_mod._client.cache_clear()


@pytest.mark.parametrize(
    "statuses, kind, attempts",
    [
        ([503, 503], "http_503", 2),      # transient: retried once, then terminal
        ([429, 429], "rate_limited", 2),
        ([401], "auth", 1),               # configuration error: never retried
        ([400], "http_400", 1),
    ],
)
def test_provider_errors_are_classified_and_retries_bounded(monkeypatch, statuses, kind, attempts):
    calls = []

    def handler(request):
        calls.append(request)
        status = statuses[min(len(calls), len(statuses)) - 1]
        return httpx.Response(status, json={"error": {"message": "secret-ish provider detail"}},
                              headers={"retry-after-ms": "1"})

    monkeypatch.setattr(generate_mod, "_client", lambda: _sdk_client(handler))
    with pytest.raises(ProviderUnavailable) as err:
        generate_answer("soru", [_package()])
    assert err.value.kind == kind
    assert len(calls) == attempts


def test_transient_failure_then_success(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(502, headers={"retry-after-ms": "1"})
        return httpx.Response(200, json=_OK_BODY)

    monkeypatch.setattr(generate_mod, "_client", lambda: _sdk_client(handler))
    assert generate_answer("soru", [_package()]).citations[0].id == "S1"
    assert len(calls) == 2


def test_connection_refused_is_unavailable(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(generate_mod, "_client", lambda: _sdk_client(handler))
    with pytest.raises(ProviderUnavailable) as err:
        generate_answer("soru", [_package()])
    assert err.value.kind == "connection"


@pytest.mark.parametrize(
    "body",
    [
        {**_OK_BODY, "choices": []},
        {**_OK_BODY, "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": None}}]},
        {**_OK_BODY, "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "  "}}]},
    ],
)
def test_empty_or_malformed_completion_is_unavailable(monkeypatch, body):
    monkeypatch.setattr(generate_mod, "_client", lambda: _sdk_client(lambda r: httpx.Response(200, json=body)))
    with pytest.raises(ProviderUnavailable) as err:
        generate_answer("soru", [_package()])
    assert err.value.kind in {"malformed", "empty"}


def test_provider_failure_log_has_no_provider_message(monkeypatch, caplog):
    monkeypatch.setattr(generate_mod, "_client", lambda: _sdk_client(
        lambda r: httpx.Response(401, json={"error": {"message": "key sk-live-123 invalid"}})))
    with pytest.raises(ProviderUnavailable):
        generate_answer("soru", [_package()])
    assert "sk-live-123" not in caplog.text
    assert "auth" in caplog.text


# ---- 3/4/6/12/14. /chat quota + session lifecycle (database faked) ----

class _Ledger:
    """Records quota and session events in order."""

    def __init__(self):
        self.events: list[str] = []
        self.open_sessions = 0

    def reserve(self, user, ip):
        self.events.append("reserve")
        return SimpleNamespace(ip=ip)

    def commit(self, reservation):
        self.events.append("commit")

    def release(self, reservation):
        self.events.append("release")

    def session_factory(self):
        ledger = self

        class _Session:
            def __enter__(self):
                ledger.open_sessions += 1
                ledger.events.append("session_open")
                return self

            def __exit__(self, *exc):
                ledger.open_sessions -= 1
                ledger.events.append("session_close")
                return False

        return _Session()


@pytest.fixture
def chat_api(monkeypatch):
    ledger = _Ledger()
    main_mod.app.dependency_overrides[get_current_user] = lambda: AuthUser(id=uuid.uuid4(), is_anonymous=True)
    monkeypatch.setattr(main_mod, "reserve_quota", ledger.reserve)
    monkeypatch.setattr(main_mod, "commit_quota", ledger.commit)
    monkeypatch.setattr(main_mod, "release_quota", ledger.release)
    monkeypatch.setattr(main_mod.db, "SessionLocal", ledger.session_factory)
    pkg = _package()

    def fake_retrieve(session, q, history=None):
        ledger.events.append("retrieve")
        return [pkg], []

    monkeypatch.setattr(retrieve_mod, "retrieve_with_notes", fake_retrieve)
    main_mod.limiter.reset()
    yield TestClient(main_mod.app, raise_server_exceptions=False), ledger
    main_mod.app.dependency_overrides.clear()


_ANSWER_Q = {"question": "faturalı 20 GB 500 TL altı", "history": []}


def test_oversized_request_is_rejected_before_endpoint_work():
    client = TestClient(main_mod.app, raise_server_exceptions=False)
    response = client.post(
        "/compare",
        content=b"x" * (settings.max_request_body_bytes + 1),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json() == {"detail": "Request body too large"}


def test_chunked_oversized_request_is_also_rejected():
    client = TestClient(main_mod.app, raise_server_exceptions=False)

    def chunks():
        yield b"x" * 20_000
        yield b"x" * 20_000

    response = client.post("/compare", content=chunks(), headers={"content-type": "application/json"})
    assert response.status_code == 413
    assert response.json() == {"detail": "Request body too large"}


def _llm(monkeypatch, ledger, behaviour):
    def create(**kwargs):
        ledger.events.append(f"llm(open_sessions={ledger.open_sessions})")
        return behaviour()
    monkeypatch.setattr(generate_mod, "_client", lambda: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))


def _ok():
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="**Öneri:** bu paket [S1]"))])


def test_success_charges_once_and_holds_no_session_during_llm(chat_api, monkeypatch):
    client, ledger = chat_api
    _llm(monkeypatch, ledger, _ok)
    res = client.post("/chat", json=_ANSWER_Q)
    assert res.status_code == 200
    assert ledger.events == ["reserve", "session_open", "retrieve", "session_close",
                             "llm(open_sessions=0)", "commit"]


def test_provider_failure_releases_and_never_charges(chat_api, monkeypatch):
    client, ledger = chat_api

    def fail():
        raise ProviderUnavailable("timeout")
    _llm(monkeypatch, ledger, fail)
    for body in (_ANSWER_Q, {"question": "merhaba", "history": []}):  # answer + clarification paths
        res = client.post("/chat", json=body)
        assert res.status_code == 503
        assert res.json() == {"detail": {"reason": "provider_unavailable"}}
    assert ledger.events.count("release") == 2 and "commit" not in ledger.events
    assert ledger.open_sessions == 0


def test_embedding_failure_is_503_and_releases(chat_api, monkeypatch):
    client, ledger = chat_api

    def broken_retrieve(session, q, history=None):
        raise EmbeddingUnavailable("model failed")
    monkeypatch.setattr(retrieve_mod, "retrieve_with_notes", broken_retrieve)
    res = client.post("/chat", json=_ANSWER_Q)
    assert res.status_code == 503 and res.json() == {"detail": {"reason": "embedding_unavailable"}}
    assert ledger.events[-2:] == ["session_close", "release"]
    assert ledger.open_sessions == 0


def test_unexpected_exception_releases_and_closes_session(chat_api, monkeypatch):
    client, ledger = chat_api

    def bug(session, q, history=None):
        raise ZeroDivisionError("bug")
    monkeypatch.setattr(retrieve_mod, "retrieve_with_notes", bug)
    res = client.post("/chat", json=_ANSWER_Q)
    assert res.status_code == 500
    assert "ZeroDivisionError" not in res.text
    assert ledger.events == ["reserve", "session_open", "session_close", "release"]


def test_database_error_is_503_without_details(chat_api, monkeypatch):
    client, ledger = chat_api
    from sqlalchemy.exc import OperationalError

    def db_down(session, q, history=None):
        raise OperationalError("SELECT secret FROM x", {"p": "v"}, Exception("host=db.internal"))
    monkeypatch.setattr(retrieve_mod, "retrieve_with_notes", db_down)
    res = client.post("/chat", json=_ANSWER_Q)
    assert res.status_code == 503 and res.json() == {"detail": {"reason": "database_unavailable"}}
    assert "release" in ledger.events and ledger.open_sessions == 0


def test_cancellation_style_exit_releases(chat_api, monkeypatch):
    """A BaseException (worker shutdown / cancellation) still releases the slot."""
    client, ledger = chat_api

    class Cancelled(BaseException):
        pass

    def cancelled(session, q, history=None):
        raise Cancelled()
    monkeypatch.setattr(retrieve_mod, "retrieve_with_notes", cancelled)
    try:
        res = client.post("/chat", json=_ANSWER_Q)
        assert res.status_code == 500
    except Cancelled:
        pass  # either surfacing is fine; what matters is the slot came back
    assert ledger.events[-1] == "release" and ledger.open_sessions == 0
    assert "commit" not in ledger.events


def test_quota_commit_failure_still_returns_the_answer(chat_api, monkeypatch):
    client, ledger = chat_api
    from sqlalchemy.exc import OperationalError

    def commit_fails(reservation):
        raise OperationalError("x", {}, Exception("down"))
    monkeypatch.setattr(main_mod, "commit_quota", commit_fails)
    _llm(monkeypatch, ledger, _ok)
    assert client.post("/chat", json=_ANSWER_Q).status_code == 200


def test_concurrent_chat_requests_each_settle_once(chat_api, monkeypatch):
    client, ledger = chat_api
    lock = threading.Lock()

    def slow_ok():
        time.sleep(0.05)
        return _ok()
    _llm(monkeypatch, ledger, slow_ok)
    monkeypatch.setattr(main_mod.limiter, "enabled", False)
    results = []

    def send():
        r = client.post("/chat", json=_ANSWER_Q)
        with lock:
            results.append(r.status_code)
    threads = [threading.Thread(target=send) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [200] * 8
    assert ledger.events.count("reserve") == ledger.events.count("commit") == 8
    assert ledger.open_sessions == 0


def test_quota_rejection_happens_before_any_work(chat_api, monkeypatch):
    client, ledger = chat_api
    from fastapi import HTTPException

    def over(user, ip):
        raise HTTPException(status_code=429, detail={"reason": "guest_limit"})
    monkeypatch.setattr(main_mod, "reserve_quota", over)
    res = client.post("/chat", json=_ANSWER_Q)
    assert res.status_code == 429 and res.json() == {"detail": {"reason": "guest_limit"}}
    assert ledger.events == []


# ---- 7/8. /compare and /packages validation ----

class _NoDB:
    def __getattr__(self, item):
        raise AssertionError("database touched for an invalid request")


class _EmptyDB:
    def scalars(self, stmt):
        return SimpleNamespace(all=lambda: [])


@pytest.fixture
def api_no_db():
    main_mod.app.dependency_overrides[get_session] = lambda: _NoDB()
    main_mod.limiter.reset()
    yield TestClient(main_mod.app)
    main_mod.app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "body",
    [
        {"package_ids": ["not-a-uuid"]},
        {"package_ids": ["1; DROP TABLE packages"]},
        {"package_ids": [str(uuid.uuid4())] * (main_mod.MAX_COMPARE_IDS + 1)},
        {"package_ids": "abc"},
        {"gb": -3},
        {"gb": "lots"},
        {"operator": "turkcel"},
        {"line_type": "both"},
    ],
)
def test_compare_rejects_malformed_input_before_the_database(api_no_db, body):
    res = api_no_db.post("/compare", json=body)
    assert res.status_code == 422


def test_compare_valid_requests_still_work():
    main_mod.app.dependency_overrides[get_session] = lambda: _EmptyDB()
    main_mod.limiter.reset()
    try:
        client = TestClient(main_mod.app)
        ids = [str(uuid.uuid4()) for _ in range(main_mod.MAX_COMPARE_IDS)]
        for body in ({}, {"package_ids": ids}, {"gb": 20, "operator": "vodafone", "line_type": "prepaid"},
                     {"customer_type": "new"}):
            assert client.post("/compare", json=body).status_code == 200
        assert client.get("/packages", params={"operator": "turkcell", "max_price": 500, "min_gb": 10}).status_code == 200
    finally:
        main_mod.app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "params",
    [{"max_price": "nan"}, {"max_price": -1}, {"min_gb": "inf"}, {"operator": "x"}, {"line_type": "x"}],
)
def test_packages_rejects_bad_query_params(api_no_db, params):
    assert api_no_db.get("/packages", params=params).status_code == 422


# ---- 9. client IP behind proxies ----

TRUSTED = _parse_trusted("10.0.0.0/8, 2001:db8::/32")


@pytest.mark.parametrize(
    "peer, xff, networks, expected",
    [
        ("203.0.113.9", ["1.2.3.4"], (), "203.0.113.9"),                  # default: header ignored
        ("203.0.113.9", ["1.2.3.4"], TRUSTED, "203.0.113.9"),             # untrusted peer: ignored
        ("10.0.0.5", ["198.51.100.7"], TRUSTED, "198.51.100.7"),           # one trusted proxy
        ("10.0.0.5", ["1.2.3.4, 198.51.100.7"], TRUSTED, "198.51.100.7"),  # spoofed left entry ignored
        ("10.0.0.5", ["1.2.3.4", "198.51.100.7, 10.0.0.9"], TRUSTED, "198.51.100.7"),  # two hops, two headers
        ("10.0.0.5", ["garbage, 198.51.100.7"], TRUSTED, "198.51.100.7"),
        ("10.0.0.5", ["198.51.100.7, not-an-ip"], TRUSTED, "10.0.0.5"),    # malformed rightmost: use the peer
        ("10.0.0.5", [""], TRUSTED, "10.0.0.5"),
        ("10.0.0.5", [], TRUSTED, "10.0.0.5"),
        ("10.0.0.5", ["10.1.1.1"], TRUSTED, "10.1.1.1"),                    # all trusted: left-most known
        ("2001:db8::1", ["2001:db8::2, 2a00:1450::5"], TRUSTED, "2a00:1450::5"),
        ("testclient", ["1.2.3.4"], TRUSTED, "testclient"),                # non-IP peer: unchanged
    ],
)
def test_resolve_client_ip(peer, xff, networks, expected):
    assert resolve_client_ip(peer, xff, networks) == expected


def test_trusted_proxies_typo_fails_loudly(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxies", "10.0.0.0/33")
    with pytest.raises(ValueError):
        trusted_networks()


def test_spoofed_header_does_not_change_the_quota_identity(chat_api, monkeypatch):
    client, ledger = chat_api
    seen = []
    monkeypatch.setattr(main_mod, "reserve_quota", lambda user, ip: seen.append(ip) or SimpleNamespace())
    _llm(monkeypatch, ledger, _ok)
    client.post("/chat", json=_ANSWER_Q, headers={"X-Forwarded-For": "1.2.3.4"})
    client.post("/chat", json=_ANSWER_Q, headers={"X-Forwarded-For": "5.6.7.8"})
    assert seen[0] == seen[1] != "1.2.3.4"


# ---- 10/11. JWT ----

_SECRET = "test-hs256-secret-with-enough-length-0123456789"


def _token(**overrides):
    now = datetime.now(timezone.utc)
    claims = {"sub": str(uuid.uuid4()), "aud": "authenticated", "iat": now, "exp": now + timedelta(hours=1),
              "is_anonymous": True}
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, _SECRET, algorithm="HS256")


@pytest.fixture
def auth_client(monkeypatch):
    monkeypatch.setattr(settings, "supabase_jwt_secret", _SECRET)
    main_mod.limiter.reset()
    return TestClient(main_mod.app)


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Basic abc"},
        {"Authorization": "Bearer "},
        {"Authorization": "Bearer not.a.jwt"},
    ],
)
def test_missing_or_malformed_token_is_401(auth_client, headers):
    assert auth_client.post("/chat", json=_ANSWER_Q, headers=headers).status_code == 401


@pytest.mark.parametrize(
    "overrides",
    [
        {"exp": datetime.now(timezone.utc) - timedelta(minutes=5)},  # expired (beyond 30 s leeway)
        {"exp": None},                                                # never-expiring token
        {"aud": "anon"},
        {"sub": None},
        {"sub": "not-a-uuid"},
    ],
)
def test_expired_or_incomplete_token_is_401(auth_client, overrides):
    res = auth_client.post("/chat", json=_ANSWER_Q, headers={"Authorization": f"Bearer {_token(**overrides)}"})
    assert res.status_code == 401


def test_hs256_rejected_when_no_secret_configured(monkeypatch):
    monkeypatch.setattr(settings, "supabase_jwt_secret", "")
    main_mod.limiter.reset()
    res = TestClient(main_mod.app).post("/chat", json=_ANSWER_Q, headers={"Authorization": f"Bearer {_token()}"})
    assert res.status_code == 401


def test_valid_token_reaches_the_quota_check(auth_client, monkeypatch):
    seen = []

    def stop(user, ip):
        seen.append(user)
        from fastapi import HTTPException
        raise HTTPException(status_code=429, detail={"reason": "guest_limit"})
    monkeypatch.setattr(main_mod, "reserve_quota", stop)
    res = auth_client.post("/chat", json=_ANSWER_Q, headers={"Authorization": f"Bearer {_token()}"})
    assert res.status_code == 429 and seen and seen[0].is_anonymous


def test_unknown_kid_refetches_jwks_at_most_once_per_interval(monkeypatch):
    rebuilds = []

    class _Client:
        def get_signing_key_from_jwt(self, token):
            raise jwt.exceptions.PyJWKClientError("unknown kid")

    fake = _Client()
    fake_factory = lambda: fake  # noqa: E731
    fake_factory.cache_clear = lambda: rebuilds.append(1)
    monkeypatch.setattr(auth_mod, "_jwk_client", fake_factory)
    monkeypatch.setattr(auth_mod, "_jwks_last_refresh", 0.0)
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.generate_private_key(ec.SECP256R1())
    token = jwt.encode({"sub": "x"}, key, algorithm="ES256", headers={"kid": "junk"})
    for _ in range(20):
        with pytest.raises(jwt.exceptions.PyJWKClientError):
            auth_mod._verify(token)
    assert len(rebuilds) == 1


# ---- 13. embedding lifecycle ----

@pytest.fixture
def fresh_embed(monkeypatch):
    monkeypatch.setattr(embed_mod, "_model_instance", None)
    yield embed_mod


def test_embedding_load_failure_is_typed_and_retried(fresh_embed, monkeypatch):
    attempts = []

    def broken_load():
        attempts.append(1)
        raise OSError("model files missing")
    monkeypatch.setattr(fresh_embed, "_load", broken_load)
    for _ in range(2):
        with pytest.raises(EmbeddingUnavailable):
            fresh_embed.embed_query("soru")
    assert len(attempts) == 2  # a failure is not cached
    assert not fresh_embed.is_loaded()


def test_concurrent_first_requests_load_the_model_once(fresh_embed, monkeypatch):
    import numpy as np
    loads = []

    class _Model:
        def encode(self, texts, **kw):
            return {"dense_vecs": np.ones((len(texts), 4))}

    def slow_load():
        loads.append(1)
        time.sleep(0.2)
        return _Model()
    monkeypatch.setattr(fresh_embed, "_load", slow_load)
    out = []
    threads = [threading.Thread(target=lambda: out.append(fresh_embed.embed_query("q"))) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(loads) == 1 and len(out) == 8


def test_encodes_are_serialized(fresh_embed, monkeypatch):
    import numpy as np
    active, peak = [0], [0]
    guard = threading.Lock()

    class _Model:
        def encode(self, texts, **kw):
            with guard:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.02)
            with guard:
                active[0] -= 1
            return {"dense_vecs": np.ones((len(texts), 4))}
    monkeypatch.setattr(fresh_embed, "_model_instance", _Model())
    threads = [threading.Thread(target=lambda: fresh_embed.embed_query("q")) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak[0] == 1


def test_background_preload_failure_does_not_crash(fresh_embed, monkeypatch):
    monkeypatch.setattr(fresh_embed, "_load", lambda: (_ for _ in ()).throw(OSError("no disk")))
    fresh_embed.preload_in_background().join(timeout=5)
    assert not fresh_embed.is_loaded()


def test_encode_error_is_typed(fresh_embed, monkeypatch):
    class _Model:
        def encode(self, texts, **kw):
            raise RuntimeError("Already borrowed")
    monkeypatch.setattr(fresh_embed, "_model_instance", _Model())
    with pytest.raises(EmbeddingUnavailable):
        fresh_embed.embed_query("q")
