import configparser
import asyncio
from dataclasses import dataclass
import os
from datetime import datetime, time, timedelta
from typing import Literal

import pytz
import anyio
from babel.dates import format_datetime, format_date, format_time, get_datetime_format, get_date_format, get_time_format
from fastapi import FastAPI, HTTPException
from fastapi.templating import Jinja2Templates
from icalevents.icalevents import events
from starlette import status
from starlette.requests import Request
from starlette.responses import HTMLResponse
from urllib3.exceptions import TimeoutError as HTTPTimeoutError

from calendar_source import CalendarDownloadTimeout, download_calendar, is_ics_url

CONFIG = configparser.ConfigParser(interpolation=configparser.ExtendedInterpolation())
CONFIG["DEFAULT"] = {"timezone": "UTC", "days to future": 40, "locale": "en_GB", "width": 300}
CONFIG["DEFAULT"].update({"max calendar bytes": "5242880", "max days": "366", "max events": "5000",
                        "download timeout": "60", "max concurrent calendars": "4"})
CONFIG.read('config.ini')
if "allow private network" in CONFIG.defaults():
    raise ValueError("allow private network must be set on an individual calendar, not DEFAULT")


@dataclass(frozen=True)
class ResourceLimits:
    max_calendar_bytes: int
    max_days: int
    max_events: int
    download_timeout: int
    max_concurrent_calendars: int

    @classmethod
    def from_config(cls):
        values = {}
        for field in cls.__dataclass_fields__:
            option = field.replace("_", " ")
            try:
                value = CONFIG["DEFAULT"].getint(option)
                if value <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                raise ValueError(f"{option} must be a positive integer in DEFAULT") from None
            values[field] = value
        return cls(**values)


LIMITS = ResourceLimits.from_config()
calendar_slots = anyio.CapacityLimiter(LIMITS.max_concurrent_calendars)
calendar_threads = anyio.CapacityLimiter(LIMITS.max_concurrent_calendars)
calendar_jobs = set()

app = FastAPI(version=os.getenv('VERSION', 'DEVEL'), name="CalVie", description="Simple iCal viewer", )
templates = Jinja2Templates(directory="templates")


@app.get("/", status_code=status.HTTP_418_IM_A_TEAPOT)
async def root():
    return {"status": "I am a teapot at default"}


def _load_calendar(url, allow_private_network, end, tz):
    content = download_calendar(url, allow_private_network=allow_private_network,
                                max_bytes=LIMITS.max_calendar_bytes, timeout=LIMITS.download_timeout)
    ev = events(string_content=content, end=end)
    if len(ev) > LIMITS.max_events:
        raise ValueError("Too many calendar events")
    # Preserve calendar dates before applying the requested display timezone.
    for event in ev:
        if event.all_day:
            start_date = event.start.date()
            end_date = event.end.date()
            if end_date == start_date:
                end_date += timedelta(days=1)
            event.start = tz.localize(datetime.combine(start_date, time.min))
            event.end = tz.localize(datetime.combine(end_date, time.min))
        else:
            event.astimezone(tz)
    return ev


async def _run_calendar_job(borrower, slots, url, allow_private_network, end, tz):
    try:
        return await anyio.to_thread.run_sync(
            _load_calendar, url, allow_private_network, end, tz,
            abandon_on_cancel=False, limiter=calendar_threads,
        )
    finally:
        slots.release_on_behalf_of(borrower)


def _calendar_job_finished(job):
    calendar_jobs.discard(job)
    if not job.cancelled():
        # Also consume failures when the requesting client has disconnected.
        job.exception()


@app.get("/cal/{name:path}")
async def cal_data(name: str, timezone: str = None, days: int = None):
    allow_private_network = False
    try:
        config = CONFIG[name]
        url = config['url']
    except KeyError:
        if not is_ics_url(name):
            raise HTTPException(404, detail="Invalid calendar name")
        url = name
        config = CONFIG["DEFAULT"]
    try:
        effective_days = days if days is not None else config.getint("days to future")
        if not 1 <= effective_days <= LIMITS.max_days:
            raise ValueError
        tz = pytz.timezone(timezone or config["timezone"])
        end = datetime.now(tz=tz) + timedelta(days=effective_days)
    except (ValueError, TypeError, OverflowError, pytz.UnknownTimeZoneError):
        raise HTTPException(422, detail="Invalid calendar range") from None
    try:
        if config.name != "DEFAULT":
            allow_private_network = config.getboolean("allow private network", fallback=False)
    except ValueError:
        raise HTTPException(400, detail="Unable to load calendar") from None
    borrower = object()
    slots = calendar_slots
    try:
        slots.acquire_on_behalf_of_nowait(borrower)
    except anyio.WouldBlock:
        raise HTTPException(503, detail="Calendar service is busy", headers={"Retry-After": "1"}) from None
    try:
        job = asyncio.create_task(_run_calendar_job(borrower, slots, url, allow_private_network, end, tz))
    except BaseException:
        slots.release_on_behalf_of(borrower)
        raise
    calendar_jobs.add(job)
    job.add_done_callback(_calendar_job_finished)
    try:
        ev = await asyncio.shield(job)
    except (CalendarDownloadTimeout, HTTPTimeoutError):
        raise HTTPException(504, detail="Calendar download timed out") from None
    except Exception:
        raise HTTPException(400, detail="Unable to load calendar") from None
    return ev


@app.get("/iframe/{name:path}", response_class=HTMLResponse)
async def iframe(request: Request, name: str, timezone: str = None, days: int = None, locale: str = None,
                 width: int = None, colour: Literal["white", "black"] = None,
                 color_scheme: Literal["normal", "light", "dark", "light dark", "dark light"] = None):
    """
    Generate an HTML iframe view of calendar events.

    Args:
        name: Calendar name from config.ini or direct .ics URL
        timezone: Override timezone for event display
        days: Number of days to show from now
        locale: Locale for date/time formatting
        width: Width of the iframe in pixels
        colour: DEPRECATED - Use color_scheme instead. Forces light ("white") or dark ("black") theme
        color_scheme: CSS color-scheme value - "light", "dark", "normal", "light dark", or "dark light"

    Returns:
        HTML response with calendar events in iframe format
    """
    try:
        data = await cal_data(name, timezone, days)
    except HTTPException as e:
        return templates.TemplateResponse(request=request, name="error.html",
                                          context={"detail": str(e), "status_code": e.status_code},
                                          status_code=e.status_code, headers=e.headers)
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
            end_date = event.end.date() - timedelta(days=1)
            start = format_date(event.start.date(), date_str, locale=locale)
            end = format_date(end_date, date_str, locale=locale)
            if event.start.date() == end_date:
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
