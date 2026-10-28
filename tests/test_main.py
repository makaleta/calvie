"""Route and source selection regressions; actual processing is covered separately."""
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

import main
from main import app, is_ics_url, CONFIG


@pytest.fixture
def test_client():
    return TestClient(app)


@pytest.fixture
def loading(monkeypatch):
    download = AsyncMock(return_value="calendar")
    processor = AsyncMock(return_value=b"[]")
    monkeypatch.setattr(main, "download_calendar", download)
    monkeypatch.setattr(main, "process_calendar", processor)
    return download, processor


class TestURLValidation:
    """Test the is_ics_url function."""
    
    def test_valid_ics_urls(self):
        """Test that valid .ics URLs are correctly identified."""
        valid_urls = [
            "https://example.com/calendar.ics",
            "http://example.com/calendar.ics",
            "https://subdomain.example.com/path/to/calendar.ics",
            "http://example.com:8080/calendar.ics",
            "https://example.com/path/calendar.ics",
            "calendar.ics"  # minimal case
        ]
        
        for url in valid_urls:
            assert is_ics_url(url), f"URL {url} should be valid"
    
    def test_invalid_ics_urls(self):
        """Test that invalid URLs are correctly rejected."""
        invalid_urls = [
            "https://example.com/calendar.txt",
            "https://example.com/",
            "not_a_url",
            "https://example.com/calendar.ics.txt",
            "",
            "ftp://example.com/calendar.ics",  # unsupported protocol
            "https://example.com/calendar",  # no .ics extension
        ]
        
        for url in invalid_urls:
            assert not is_ics_url(url), f"URL {url} should be invalid"


class TestRootEndpoint:
    """Test the root endpoint."""
    
    def test_root_endpoint_returns_teapot(self, test_client):
        """Test that root endpoint returns HTTP 418 with teapot message."""
        response = test_client.get("/")
        
        assert response.status_code == 418
        assert response.json() == {"status": "I am a teapot at default"}



@pytest.mark.parametrize("name", ["test", "https://example.com/direct.ics"])
def test_calendar_source_selection_and_parameters(loading, test_client, name):
    CONFIG["test"] = {"url": "https://example.com/test.ics"}
    response = test_client.get(f"/cal/{name}", params={"timezone": "Europe/London", "days": 7})
    assert response.status_code == 200
    assert response.json() == []
    assert response.headers["content-type"] == "application/json"
    loading[0].assert_called_once()
    payload = loading[1].call_args.args[0]
    assert payload["timezone"] == "Europe/London"
    assert payload["format"] == "json"
    assert payload["content"] == "calendar"
    assert "url" not in payload


def test_invalid_calendar_name_does_not_download(loading, test_client):
    assert test_client.get("/cal/nonexistent").status_code == 404
    loading[0].assert_not_called()


@pytest.mark.parametrize("route", ["cal", "iframe"])
@pytest.mark.parametrize("stage", [0, 1])
def test_loading_errors_are_generic(loading, test_client, route, stage):
    CONFIG["test"] = {"url": "https://example.com/SECRET.ics"}
    loading[stage].side_effect = ValueError("SECRET")
    response = test_client.get(f"/{route}/test")
    assert response.status_code == 400
    assert "Unable to load calendar" in response.text
    assert "SECRET" not in response.text


@pytest.mark.parametrize("params", [{"timezone": "invalid"}, {"locale": "invalid"}])
def test_invalid_iframe_options_rejected_before_download(loading, test_client, params):
    CONFIG["test"] = {"url": "https://example.com/test.ics"}
    assert test_client.get("/iframe/test", params=params).status_code == 422
    loading[0].assert_not_called()


def test_default_config_values():
    assert dict(CONFIG.defaults()).items() >= {"timezone": "UTC", "days to future": "40",
                                              "locale": "en_GB", "width": "300"}.items()
