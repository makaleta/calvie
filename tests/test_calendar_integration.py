"""Exercise the real parser and HTTP rendering without external calendar access."""
from datetime import datetime
from unittest.mock import patch

import pytest
import pytz
from fastapi.testclient import TestClient

import main

CALENDAR = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Calvie//Regression//EN
BEGIN:VEVENT
UID:timed
DTSTART:20260105T100000Z
DTEND:20260105T110000Z
SUMMARY:Meeting <team>
END:VEVENT
BEGIN:VEVENT
UID:all-day
DTSTART;VALUE=DATE:20260104
DTEND;VALUE=DATE:20260105
SUMMARY:Day off
END:VEVENT
BEGIN:VEVENT
UID:recurring
DTSTART:20260106T120000Z
DTEND:20260106T123000Z
RRULE:FREQ=DAILY;COUNT=2
SUMMARY:Daily meeting
END:VEVENT
END:VCALENDAR
"""


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 1, 1, tzinfo=pytz.UTC).astimezone(tz)


@pytest.fixture
def calendar_client(monkeypatch, isolated_config):
    isolated_config['test'] = {'url': 'https://example.com/test.ics'}
    monkeypatch.setattr(main, 'datetime', FixedDatetime)
    monkeypatch.setattr('icalevents.icalparser.now', lambda: FixedDatetime.now(pytz.UTC))
    with patch('icalevents.icalevents.ICalDownload.data_from_url', return_value=CALENDAR):
        with TestClient(main.app) as client:
            yield client


@pytest.mark.parametrize('name', ['test', 'https://example.com/direct.ics'])
def test_real_calendar_json(calendar_client, name):
    response = calendar_client.get(f'/cal/{name}', params={'timezone': 'Europe/Prague'})
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 4
    meeting = next(event for event in data if event['summary'] == 'Meeting <team>')
    assert meeting['start'] == '2026-01-05T11:00:00+01:00'
    assert meeting['end'] == '2026-01-05T12:00:00+01:00'
    assert meeting['all_day'] is False
    recurring = sorted(event['start'] for event in data if event['summary'] == 'Daily meeting')
    assert recurring == ['2026-01-06T13:00:00+01:00', '2026-01-07T13:00:00+01:00']


def test_real_calendar_iframe(calendar_client):
    response = calendar_client.get('/iframe/test', params={'timezone': 'UTC', 'locale': 'en_GB'})
    assert response.status_code == 200
    html = response.text
    assert 'Meeting &lt;team&gt;' in html
    assert 'Mon 5 Jan, 10:00 - 11:00' in html
    assert 'Sun 4 Jan</li>' in html
    assert html.index('Day off') < html.index('Meeting &lt;team&gt;') < html.index('Daily meeting')


def test_days_limits_real_parser_window(calendar_client):
    response = calendar_client.get('/cal/test', params={'days': 5})
    assert response.status_code == 200
    assert {event['summary'] for event in response.json()} == {'Day off', 'Meeting <team>'}


@pytest.mark.parametrize('timezone', ['UTC', 'Europe/Prague', 'America/New_York', 'Pacific/Kiritimati'])
def test_all_day_calendar_date_in_non_utc_timezone(calendar_client, timezone):
    response = calendar_client.get('/iframe/test', params={'timezone': timezone, 'locale': 'en_GB'})
    assert response.status_code == 200
    assert '<li class="event-time">Sun 4 Jan</li>' in response.text


@pytest.mark.parametrize('calendar_timezone', ['', 'X-WR-TIMEZONE:Europe/Prague\n', 'X-WR-TIMEZONE:America/New_York\n'])
@pytest.mark.parametrize('timezone', ['Europe/Prague', 'America/New_York'])
@pytest.mark.parametrize('end_property,expected', [
    ('DTEND;VALUE=DATE:20260330', ['Sun 29 Mar']),
    ('DTEND;VALUE=DATE:20260331', ['Sun 29 Mar - Mon 30 Mar']),
    ('DURATION:P2D', ['Sun 29 Mar - Mon 30 Mar']),
    ('', ['Sun 29 Mar']),
    ('DTEND;VALUE=DATE:20260330\nRRULE:FREQ=DAILY;COUNT=2', ['Sun 29 Mar', 'Mon 30 Mar']),
])
def test_all_day_ranges_across_dst(calendar_client, calendar_timezone, timezone, end_property, expected):
    calendar = ('BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//Calvie//Test//EN\n'
                + calendar_timezone + 'BEGIN:VEVENT\nUID:day\nDTSTART;VALUE=DATE:20260329\n'
                + end_property + '\nSUMMARY:Holiday\nEND:VEVENT\nEND:VCALENDAR\n')
    with patch('icalevents.icalevents.ICalDownload.data_from_url', return_value=calendar):
        params = {'timezone': timezone, 'locale': 'en_GB', 'days': 100}
        response = calendar_client.get('/iframe/test', params=params)
        assert response.status_code == 200
        for interval in expected:
            assert f'<li class="event-time">{interval}</li>' in response.text
        data = calendar_client.get('/cal/test', params=params).json()
    assert len(data) == len(expected)
    for event in data:
        start = datetime.fromisoformat(event['start'])
        end = datetime.fromisoformat(event['end'])
        assert start.hour == end.hour == 0
        assert end.date() > start.date()
        # Midnight on the DST transition has a different offset from the next day.
        zone = pytz.timezone(timezone)
        assert start.utcoffset() == zone.localize(start.replace(tzinfo=None)).utcoffset()
        assert end.utcoffset() == zone.localize(end.replace(tzinfo=None)).utcoffset()
