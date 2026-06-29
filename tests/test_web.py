from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from reklama.engine import CampaignEngine


@pytest.fixture
def mock_modules():
    with (
        patch.dict("sys.modules", {
            "run": MagicMock(engine=CampaignEngine()),
            "search": MagicMock(search_state={
                "running": False, "finished": False, "state": "",
                "timer_total": 0.0, "timer_remaining": 0.0,
                "current_group": "", "delay_multiplier": 1.0,
                "sent": 0, "skipped": 0, "errors": 0, "total": 0,
                "active_hours": "", "joined": 0, "found": 0,
            }),
        }),
    ):
        import importlib

        import web as web_mod
        importlib.reload(web_mod)
        yield web_mod


@pytest.mark.asyncio
async def test_get_status_idle(mock_modules):
    web_mod = mock_modules
    transport = ASGITransport(app=web_mod.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["campaign"]["status"] == "idle"
        assert data["search"]["status"] == "idle"


@pytest.mark.asyncio
async def test_get_status_no_credentials(mock_modules, monkeypatch):
    web_mod = mock_modules
    monkeypatch.setattr(web_mod.config, "load_accounts", lambda: [])
    transport = ASGITransport(app=web_mod.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/status")
        data = resp.json()
        assert data["auth"]["status"] == "no_credentials"
        assert data["accounts"] == []


@pytest.mark.asyncio
async def test_get_config(mock_modules, tmp_path, monkeypatch):
    web_mod = mock_modules
    monkeypatch.setattr(web_mod.config, "BASE_DIR", tmp_path)
    env_file = tmp_path / ".env"
    env_file.write_text("TELEGRAM_API_ID=123\nDELAY_MIN_SEC=30\n# comment\n", encoding="utf-8")
    transport = ASGITransport(app=web_mod.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["TELEGRAM_API_ID"] == "123"
        assert data["DELAY_MIN_SEC"] == "30"


@pytest.mark.asyncio
async def test_post_config(mock_modules, tmp_path, monkeypatch):
    web_mod = mock_modules
    monkeypatch.setattr(web_mod.config, "BASE_DIR", tmp_path)
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    transport = ASGITransport(app=web_mod.app)
    with patch("importlib.reload", return_value=None):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/config", json={"settings": {"DELAY_MIN_SEC": "45"}})
            assert resp.status_code == 200
            content = env_file.read_text(encoding="utf-8")
            assert "DELAY_MIN_SEC=45" in content


@pytest.mark.asyncio
async def test_get_message_content(mock_modules, tmp_path, monkeypatch):
    web_mod = mock_modules
    monkeypatch.setattr(web_mod.config, "BASE_DIR", tmp_path)
    msg_file = tmp_path / "message.txt"
    msg_file.write_text("Hello World", encoding="utf-8")
    transport = ASGITransport(app=web_mod.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/message")
        assert resp.status_code == 200
        assert resp.json()["text"] == "Hello World"


@pytest.mark.asyncio
async def test_post_message_content(mock_modules, tmp_path, monkeypatch):
    web_mod = mock_modules
    monkeypatch.setattr(web_mod.config, "BASE_DIR", tmp_path)
    transport = ASGITransport(app=web_mod.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/message", json={"text": "New message"})
        assert resp.status_code == 200
        msg_file = tmp_path / "message.txt"
        assert msg_file.read_text(encoding="utf-8") == "New message"


@pytest.mark.asyncio
async def test_campaign_stop_when_not_running(mock_modules):
    web_mod = mock_modules
    web_mod.state.campaign_task = None
    transport = ASGITransport(app=web_mod.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/campaign/stop")
        assert resp.status_code == 200
        assert resp.json()["ok"] is False


@pytest.mark.asyncio
async def test_campaign_pause_when_not_running(mock_modules):
    web_mod = mock_modules
    web_mod.state.campaign_task = None
    transport = ASGITransport(app=web_mod.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/campaign/pause")
        assert resp.status_code == 200
        assert resp.json()["ok"] is False


@pytest.mark.asyncio
async def test_campaign_resume_when_not_running(mock_modules):
    web_mod = mock_modules
    web_mod.state.campaign_task = None
    transport = ASGITransport(app=web_mod.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/campaign/resume")
        assert resp.status_code == 200
        assert resp.json()["ok"] is False


@pytest.mark.asyncio
async def test_skip_delay_when_not_running(mock_modules):
    web_mod = mock_modules
    web_mod.state.campaign_task = None
    transport = ASGITransport(app=web_mod.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/campaign/skip-delay")
        assert resp.status_code == 200
        assert resp.json()["ok"] is False


@pytest.mark.asyncio
async def test_search_stop_when_not_running(mock_modules):
    web_mod = mock_modules
    web_mod.state.search_task = None
    transport = ASGITransport(app=web_mod.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/search/stop")
        assert resp.status_code == 200
        assert resp.json()["ok"] is False


@pytest.mark.asyncio
async def test_get_logs(mock_modules):
    web_mod = mock_modules
    transport = ASGITransport(app=web_mod.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/logs")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_index_missing_template(mock_modules, tmp_path, monkeypatch):
    web_mod = mock_modules
    monkeypatch.setattr(web_mod, "TEMPLATES_DIR", tmp_path)
    transport = ASGITransport(app=web_mod.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/")
        assert resp.status_code == 404
