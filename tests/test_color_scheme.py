"""Regression coverage for automatic and explicitly selected iframe themes."""
from unittest.mock import AsyncMock, patch

import main
import calendar_worker

import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture(autouse=True)
def render_in_process(monkeypatch, isolated_config):
    isolated_config['test'] = {'url': 'https://example.com/calendar.ics'}
    async def process(payload, **_kwargs):
        return ''.join(calendar_worker.response_chunks(payload)).encode()
    monkeypatch.setattr(main, 'process_calendar', process)


@pytest.mark.parametrize('scheme', ['light dark', 'dark light'])
def test_automatic_scheme_scopes_dark_colors_to_media_query(scheme):
    with patch('main.download_calendar', new_callable=AsyncMock, return_value='BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR\n'):
        response = TestClient(app).get('/iframe/test', params={'color_scheme': scheme})
    assert response.status_code == 200
    css = response.text.split('<style>')[1].split('</style>')[0]
    before, dark_rules = css.split('@media (prefers-color-scheme: dark)')
    assert 'background-color: #111' not in before
    assert 'background-color: #111' in dark_rules
    assert 'color: ghostwhite' in dark_rules


def test_normal_does_not_enable_dark_theme():
    with patch('main.download_calendar', new_callable=AsyncMock, return_value='BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR\n'):
        response = TestClient(app).get('/iframe/test', params={'color_scheme': 'normal', 'colour': 'black'})
    assert response.status_code == 200
    assert 'color-scheme: normal' in response.text
    assert '@media (prefers-color-scheme: dark)' not in response.text
    assert 'background-color: #111' not in response.text


def test_default_scheme_remains_automatic():
    with patch('main.download_calendar', new_callable=AsyncMock, return_value='BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR\n'):
        response = TestClient(app).get('/iframe/test')
    assert response.status_code == 200
    assert 'color-scheme: light dark' in response.text
    assert '@media (prefers-color-scheme: dark)' in response.text


def test_invalid_scheme_is_rejected_before_loading_calendar():
    with patch('main._calendar_response') as load:
        response = TestClient(app).get('/iframe/test', params={'color_scheme': 'invalid'})
    assert response.status_code == 422
    load.assert_not_called()


@pytest.mark.parametrize('params,css_scheme,dark', [
    ({'color_scheme': 'light'}, 'light', False),
    ({'color_scheme': 'dark'}, 'dark', True),
    ({'colour': 'white'}, 'light only', False),
    ({'colour': 'black'}, 'dark only', True),
    ({'colour': 'black', 'color_scheme': 'light'}, 'light', False),
    ({'colour': 'white', 'color_scheme': 'dark'}, 'dark', True),
])
def test_explicit_and_legacy_theme_compatibility(params, css_scheme, dark):
    with patch('main.download_calendar', new_callable=AsyncMock, return_value='BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR\n'):
        response = TestClient(app).get('/iframe/test', params=params)
    assert response.status_code == 200
    assert f'color-scheme: {css_scheme};' in response.text
    assert ('background-color: #111' in response.text) is dark
    assert '@media (prefers-color-scheme: dark)' not in response.text


def test_custom_locale_and_width_preserved():
    with patch('main.download_calendar', new_callable=AsyncMock, return_value='BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR\n'):
        response = TestClient(app).get('/iframe/test', params={'locale': 'de_DE', 'width': 400})
    assert response.status_code == 200
    assert '<html lang="de_DE">' in response.text
    assert 'width: 400px;' in response.text
