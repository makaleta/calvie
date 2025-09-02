import configparser
import os
import re
from datetime import datetime, timedelta
from typing import Literal

import pytz
from babel.dates import format_datetime, format_date, format_time, get_datetime_format, get_date_format, get_time_format
from fastapi import FastAPI, HTTPException
from fastapi.templating import Jinja2Templates
from icalevents.icalevents import events
from starlette import status
from starlette.requests import Request
from starlette.responses import HTMLResponse

CONFIG = configparser.ConfigParser(interpolation=configparser.ExtendedInterpolation())
CONFIG["DEFAULT"] = {"timezone": "UTC", "days to future": 40, "locale": "en_GB", "width": 300}
CONFIG.read('config.ini')

app = FastAPI(version=os.getenv('VERSION', 'DEVEL'), name="CalVie", description="Simple iCal viewer", )
templates = Jinja2Templates(directory="templates")


@app.get("/", status_code=status.HTTP_418_IM_A_TEAPOT)
async def root():
    return {"status": "I am a teapot at default"}


def is_ics_url(name: str) -> bool:
    """Check if the given string is a URL pointing to an .ics file."""
    ics_url_pattern = re.compile(r'^(https?://)?'  # http:// or https:// (optional)
                                 r'([a-zA-Z0-9.-]+)'  # domain name
                                 r'(:[0-9]{1,5})?'  # port (optional)
                                 r'(/.*)?'  # path (optional)
                                 r'\.ics$'  # ends with .ics
                                 )
    return bool(ics_url_pattern.match(name))


@app.get("/cal/{name:path}")
async def cal_data(name: str, timezone: str = None, days: int = None):
    try:
        config = CONFIG[name]
        url = config['url']
    except KeyError:
        if not is_ics_url(name):
            raise HTTPException(404, detail="Invalid calendar name")
        url = name
        config = CONFIG["DEFAULT"]
    tz = pytz.timezone(timezone or config["timezone"])
    end = datetime.now(tz=tz) + timedelta(days=days or int(config["days to future"]))
    try:
        ev = events(url, tzinfo=tz, end=end)
    except Exception as e:
        raise HTTPException(400, detail=str(e))
    return ev


@app.get("/iframe/{name:path}", response_class=HTMLResponse)
async def iframe(request: Request, name: str, timezone: str = None, days: int = None, locale: str = None,
                 width: int = None, colour: Literal["white", "black"] = None, 
                 color_scheme: Literal["normal", "light", "dark", "light dark", "dark light"] = None):
    try:
        data = await cal_data(name, timezone, days)
    except HTTPException as e:
        return templates.TemplateResponse(request=request, name="error.html",
                                          context={"detail": str(e), "status_code": e.status_code},
                                          status_code=e.status_code)
    # sieve and localize data
    try:
        config = CONFIG[name]
    except KeyError:
        config = CONFIG["DEFAULT"]
    header_language = request.headers.get("accept-languages", "").split(",")[0].replace("-", "_").strip()
    locale = locale or header_language or config["locale"]
    tz = pytz.timezone(timezone or config["timezone"])
    width = width or int(config["width"])

    # Handle color scheme - prioritize color_scheme over colour (deprecated)
    # Map color_scheme values to template values for backward compatibility
    if color_scheme is not None:
        if color_scheme == "light":
            effective_scheme = "white"
        elif color_scheme == "dark":
            effective_scheme = "black"
        else:  # "normal", "light dark", "dark light"
            effective_scheme = None
    else:
        # Fall back to deprecated colour parameter
        effective_scheme = colour

    def localize(event) -> str:
        date_str = str(get_date_format("medium", locale=locale))
        date_str = date_str.replace("MMMM", "MMM")
        date_str = date_str.replace("y", "")
        date_str = date_str.strip(", ")
        date_str = f"EEE {date_str}"
        time_str = str(get_time_format("short", locale=locale))
        datetime_str = str(get_datetime_format("long", locale=locale)).format(time_str, date_str)
        if event.all_day:
            end_datetime = event.end - timedelta(seconds=1)
            start = format_date(event.start.date(), date_str, locale=locale)
            end = format_date(end_datetime.date(), date_str, locale=locale)
            if event.start.date() == end_datetime.date():
                return start
            else:
                return f"{start} - {end}"
        start = format_datetime(event.start, datetime_str, locale=locale, tzinfo=tz)
        if event.start.date() == event.end.date():
            end = format_time(event.end, time_str, locale=locale, tzinfo=tz)
        else:
            end = format_datetime(event.end, datetime_str, locale=locale, tzinfo=tz)
        return f"{start} - {end}"

    data = sorted(data, key=lambda event: event.start)
    ev_minimal = [{"summary": e.summary, "interval": localize(e)} for e in data]
    return templates.TemplateResponse(request=request, name="iframe.html",
                                      context={"events": ev_minimal, "lang": locale, "width": width,
                                               "force_scheme": effective_scheme, "color_scheme": color_scheme}, 
                                      status_code=status.HTTP_200_OK)
