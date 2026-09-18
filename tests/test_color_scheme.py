"""Regression coverage for automatic and explicitly selected iframe themes."""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.mark.parametrize('scheme', ['light dark', 'dark light'])
def test_automatic_scheme_scopes_dark_colors_to_media_query(scheme):
    with patch('main.cal_data', return_value=[]):
        response = TestClient(app).get('/iframe/test', params={'color_scheme': scheme})
    assert response.status_code == 200
    css = response.text.split('<style>')[1].split('</style>')[0]
    before, dark_rules = css.split('@media (prefers-color-scheme: dark)')
    assert 'background-color: #111' not in before
    assert 'background-color: #111' in dark_rules
    assert 'color: ghostwhite' in dark_rules


def test_normal_does_not_enable_dark_theme():
    with patch('main.cal_data', return_value=[]):
        response = TestClient(app).get('/iframe/test', params={'color_scheme': 'normal', 'colour': 'black'})
    assert response.status_code == 200
    assert 'color-scheme: normal' in response.text
    assert '@media (prefers-color-scheme: dark)' not in response.text
    assert 'background-color: #111' not in response.text


def test_default_scheme_remains_automatic():
    with patch('main.cal_data', return_value=[]):
        response = TestClient(app).get('/iframe/test')
    assert response.status_code == 200
    assert 'color-scheme: light dark' in response.text
    assert '@media (prefers-color-scheme: dark)' in response.text


def test_invalid_scheme_is_rejected_before_loading_calendar():
    with patch('main.cal_data') as load:
        response = TestClient(app).get('/iframe/test', params={'color_scheme': 'invalid'})
    assert response.status_code == 422
    load.assert_not_called()
