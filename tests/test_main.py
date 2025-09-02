"""
Unit tests for the Calvie iCal viewer application.
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timedelta
import pytz

from fastapi.testclient import TestClient
from main import app, is_ics_url, CONFIG


@pytest.fixture
def test_client():
    """Fixture to provide test client."""
    return TestClient(app)


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


class TestCalDataEndpoint:
    """Test the calendar data endpoint."""
    
    @patch('main.events')
    def test_cal_data_with_configured_calendar(self, mock_events, test_client):
        """Test calendar data retrieval with configured calendar."""
        # Mock the config to have a test calendar
        with patch.dict(CONFIG, {'test_cal': {'url': 'https://example.com/test.ics', 'timezone': 'UTC', 'days to future': '30'}}):
            # Mock the events function to return empty data to avoid serialization issues
            mock_events.return_value = []
            
            response = test_client.get("/cal/test_cal")
            
            assert response.status_code == 200
            assert response.json() == []
            mock_events.assert_called_once()
    
    @patch('main.events')
    def test_cal_data_with_direct_ics_url(self, mock_events, test_client):
        """Test calendar data retrieval with direct .ics URL."""
        # Mock the events function to return empty data
        mock_events.return_value = []
        
        response = test_client.get("/cal/https://example.com/direct.ics")
        
        assert response.status_code == 200
        assert response.json() == []
        mock_events.assert_called_once()
    
    def test_cal_data_invalid_calendar_name(self, test_client):
        """Test that invalid calendar names return 404."""
        response = test_client.get("/cal/nonexistent_calendar")
        
        assert response.status_code == 404
        assert "Invalid calendar name" in response.json()["detail"]
    
    @patch('main.events')
    def test_cal_data_with_parameters(self, mock_events, test_client):
        """Test calendar data endpoint with timezone and days parameters."""
        with patch.dict(CONFIG, {'test_cal': {'url': 'https://example.com/test.ics', 'timezone': 'UTC', 'days to future': '30'}}):
            mock_events.return_value = []
            
            response = test_client.get("/cal/test_cal?timezone=Europe/London&days=7")
            
            assert response.status_code == 200
            mock_events.assert_called_once()
            # Verify that the timezone and days parameters were processed
            call_args = mock_events.call_args
            assert call_args[1]['tzinfo'].zone == 'Europe/London'
    
    @patch('main.events')
    def test_cal_data_events_exception(self, mock_events, test_client):
        """Test that exceptions from events function return 400."""
        with patch.dict(CONFIG, {'test_cal': {'url': 'https://example.com/test.ics', 'timezone': 'UTC', 'days to future': '30'}}):
            mock_events.side_effect = Exception("Calendar parsing error")
            
            response = test_client.get("/cal/test_cal")
            
            assert response.status_code == 400
            assert "Calendar parsing error" in response.json()["detail"]


class TestIframeEndpoint:
    """Test the iframe endpoint."""
    
    @patch('main.cal_data')
    def test_iframe_success(self, mock_cal_data, test_client):
        """Test successful iframe rendering."""
        # Mock calendar data
        mock_event = Mock()
        mock_event.start = datetime(2024, 1, 1, 10, 0, 0, tzinfo=pytz.UTC)
        mock_event.end = datetime(2024, 1, 1, 11, 0, 0, tzinfo=pytz.UTC)
        mock_event.summary = "Test Event"
        mock_event.all_day = False
        mock_cal_data.return_value = [mock_event]
        
        with patch.dict(CONFIG, {'test_cal': {'timezone': 'UTC', 'locale': 'en_GB', 'width': '300'}}):
            response = test_client.get("/iframe/test_cal")
            
            assert response.status_code == 200
            assert "text/html" in response.headers["content-type"]
    
    @patch('main.cal_data')
    def test_iframe_with_parameters(self, mock_cal_data, test_client):
        """Test iframe with custom parameters."""
        mock_cal_data.return_value = []
        
        response = test_client.get("/iframe/test_cal?width=400&colour=black&locale=de_DE")
        
        assert response.status_code == 200
    
    @patch('main.cal_data')
    def test_iframe_error_handling(self, mock_cal_data, test_client):
        """Test iframe error handling when cal_data fails."""
        from fastapi import HTTPException
        mock_cal_data.side_effect = HTTPException(status_code=404, detail="Calendar not found")
        
        response = test_client.get("/iframe/nonexistent")
        
        assert response.status_code == 404
        assert "text/html" in response.headers["content-type"]  # Error template should be HTML
    
    @patch('main.cal_data')
    def test_iframe_with_color_scheme_light(self, mock_cal_data, test_client):
        """Test iframe with new color_scheme=light parameter."""
        mock_cal_data.return_value = []
        
        response = test_client.get("/iframe/test_cal?color_scheme=light")
        
        assert response.status_code == 200
        assert "color-scheme: light" in response.text
    
    @patch('main.cal_data')
    def test_iframe_with_color_scheme_dark(self, mock_cal_data, test_client):
        """Test iframe with new color_scheme=dark parameter."""
        mock_cal_data.return_value = []
        
        response = test_client.get("/iframe/test_cal?color_scheme=dark")
        
        assert response.status_code == 200
        assert "color-scheme: dark" in response.text
        assert "background-color: #111" in response.text
        assert "color: ghostwhite" in response.text
    
    @patch('main.cal_data')
    def test_iframe_with_color_scheme_light_dark(self, mock_cal_data, test_client):
        """Test iframe with new color_scheme=light dark parameter."""
        mock_cal_data.return_value = []
        
        response = test_client.get("/iframe/test_cal?color_scheme=light%20dark")
        
        assert response.status_code == 200
        assert "color-scheme: light dark" in response.text
    
    @patch('main.cal_data')
    def test_iframe_with_color_scheme_normal(self, mock_cal_data, test_client):
        """Test iframe with new color_scheme=normal parameter."""
        mock_cal_data.return_value = []
        
        response = test_client.get("/iframe/test_cal?color_scheme=normal")
        
        assert response.status_code == 200
        assert "color-scheme: normal" in response.text
        assert "@media (prefers-color-scheme: dark)" in response.text
    
    @patch('main.cal_data')
    def test_iframe_color_scheme_priority_over_colour(self, mock_cal_data, test_client):
        """Test that color_scheme parameter takes priority over deprecated colour parameter."""
        mock_cal_data.return_value = []
        
        # Both parameters provided - color_scheme should take priority
        response = test_client.get("/iframe/test_cal?colour=black&color_scheme=light")
        
        assert response.status_code == 200
        assert "color-scheme: light" in response.text
        # Should not have dark styles since color_scheme=light overrides colour=black
        assert "background-color: #111" not in response.text
    
    @patch('main.cal_data')
    def test_iframe_colour_backward_compatibility(self, mock_cal_data, test_client):
        """Test that deprecated colour parameter still works when color_scheme is not provided."""
        mock_cal_data.return_value = []
        
        # Test colour=black still works
        response = test_client.get("/iframe/test_cal?colour=black")
        
        assert response.status_code == 200
        assert "color-scheme: dark only" in response.text
        assert "background-color: #111" in response.text
        
        # Test colour=white still works
        response = test_client.get("/iframe/test_cal?colour=white")
        
        assert response.status_code == 200
        assert "color-scheme: light only" in response.text
        assert "background-color: #111" not in response.text


class TestConfiguration:
    """Test configuration handling."""
    
    def test_default_config_values(self):
        """Test that default configuration values are set."""
        defaults = CONFIG["DEFAULT"]
        
        assert defaults["timezone"] == "UTC"
        assert defaults["days to future"] == "40"
        assert defaults["locale"] == "en_GB"
        assert defaults["width"] == "300"


class TestDateLocalization:
    """Test date and time localization functionality."""
    
    def test_all_day_event_same_day(self):
        """Test localization of all-day event on same day."""
        # This tests the localize function indirectly through iframe endpoint
        mock_event = Mock()
        mock_event.start = datetime(2024, 1, 1, tzinfo=pytz.UTC)
        mock_event.end = datetime(2024, 1, 2, tzinfo=pytz.UTC)  # All-day events end at start of next day
        mock_event.summary = "All Day Event"
        mock_event.all_day = True
        
        with patch('main.cal_data') as mock_cal_data:
            mock_cal_data.return_value = [mock_event]
            
            client = TestClient(app)
            response = client.get("/iframe/test")
            
            assert response.status_code == 200
    
    def test_timed_event_same_day(self):
        """Test localization of timed event on same day."""
        mock_event = Mock()
        mock_event.start = datetime(2024, 1, 1, 10, 0, 0, tzinfo=pytz.UTC)
        mock_event.end = datetime(2024, 1, 1, 11, 0, 0, tzinfo=pytz.UTC)
        mock_event.summary = "Timed Event"
        mock_event.all_day = False
        
        with patch('main.cal_data') as mock_cal_data:
            mock_cal_data.return_value = [mock_event]
            
            client = TestClient(app)
            response = client.get("/iframe/test")
            
            assert response.status_code == 200


if __name__ == "__main__":
    pytest.main([__file__])