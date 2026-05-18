import httpx
import pytest

from scripts.novel_eval._common import OvClient, OvHttpError


class Recorder:
    """A capturing transport that returns the next queued response."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.queue: list[httpx.Response] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.queue.pop(0)


@pytest.fixture
def transport_recorder():
    return Recorder()


class TestOvClientAuth:
    def test_omits_identity_query_when_not_provided(self, transport_recorder):
        transport_recorder.queue.append(httpx.Response(200, json={"ok": True}))
        client = OvClient(
            base_url="http://server.local:8000",
            user_api_key="test-key",
            transport=httpx.MockTransport(transport_recorder),
        )

        client.post_json("/api/v1/resources", json={"x": 1})

        sent = transport_recorder.requests[0]
        assert sent.headers["X-API-Key"] == "test-key"
        assert sent.url.params.get("account_id") is None
        assert sent.url.params.get("user_id") is None

    def test_attaches_api_key_header_and_identity_query(self, transport_recorder):
        transport_recorder.queue.append(
            httpx.Response(200, json={"status": "ok", "result": {"resource_uri": "viking://x"}})
        )
        client = OvClient(
            base_url="http://server.local:8000",
            user_api_key="test-key",
            account_id="acc-A",
            user_id="user-1",
            transport=httpx.MockTransport(transport_recorder),
        )

        resp = client.post_json("/api/v1/resources", json={"path": "x"})
        assert resp == {"status": "ok", "result": {"resource_uri": "viking://x"}}

        sent = transport_recorder.requests[0]
        assert sent.headers["X-API-Key"] == "test-key"
        assert sent.url.params.get("account_id") == "acc-A"
        assert sent.url.params.get("user_id") == "user-1"


class TestOvClientRetry:
    def test_retries_on_5xx_then_succeeds(self, transport_recorder, monkeypatch):
        import scripts.novel_eval._common as common

        sleeps: list[float] = []
        monkeypatch.setattr(common.time, "sleep", lambda s: sleeps.append(s))

        transport_recorder.queue = [
            httpx.Response(503, text="boom"),
            httpx.Response(200, json={"ok": True}),
        ]
        client = OvClient(
            base_url="http://x",
            user_api_key="k",
            account_id="a",
            user_id="u",
            transport=httpx.MockTransport(transport_recorder),
        )
        out = client.post_json("/api/v1/resources", json={})
        assert out == {"ok": True}
        assert sleeps == [1.0]

    def test_raises_after_all_retries(self, transport_recorder, monkeypatch):
        import scripts.novel_eval._common as common

        monkeypatch.setattr(common.time, "sleep", lambda s: None)

        transport_recorder.queue = [
            httpx.Response(500, text="e1"),
            httpx.Response(500, text="e2"),
            httpx.Response(500, text="e3"),
        ]
        client = OvClient(
            base_url="http://x",
            user_api_key="k",
            account_id="a",
            user_id="u",
            transport=httpx.MockTransport(transport_recorder),
        )
        with pytest.raises(OvHttpError) as ei:
            client.post_json("/api/v1/resources", json={})
        assert "500" in str(ei.value)
        assert len(transport_recorder.requests) == 3

    def test_does_not_retry_on_4xx(self, transport_recorder, monkeypatch):
        import scripts.novel_eval._common as common

        monkeypatch.setattr(common.time, "sleep", lambda s: None)

        transport_recorder.queue = [httpx.Response(400, text="bad")]
        client = OvClient(
            base_url="http://x",
            user_api_key="k",
            account_id="a",
            user_id="u",
            transport=httpx.MockTransport(transport_recorder),
        )
        with pytest.raises(OvHttpError):
            client.post_json("/api/v1/resources", json={})
        assert len(transport_recorder.requests) == 1
