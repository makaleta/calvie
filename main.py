import configparser
import asyncio
from dataclasses import dataclass
import os
from datetime import datetime, timedelta
from typing import Literal

import pytz
import anyio
from babel import Locale, UnknownLocaleError
from fastapi import FastAPI, HTTPException
from fastapi.templating import Jinja2Templates
from starlette import status
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response


from calendar_source import CalendarDownloadTimeout, download_calendar, is_ics_url
from calendar_processing import (CalendarProcessStartError, CalendarProcessingTimeout,
                                 CalendarProcessingCancelled, process_calendar, require_linux)

require_linux()

CONFIG = configparser.ConfigParser(interpolation=configparser.ExtendedInterpolation())
CONFIG["DEFAULT"] = {"timezone": "UTC", "days to future": 40, "locale": "en_GB", "width": 300}
CONFIG["DEFAULT"].update({"max calendar bytes": "5242880", "max days": "366", "max events": "5000",
                        "download timeout": "60", "max concurrent calendars": "4",
                        "parser timeout": "10", "parser memory mib": "256", "max response bytes": "20971520"})
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
    parser_timeout: int
    parser_memory_mib: int
    max_response_bytes: int

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
calendar_jobs = set()

app = FastAPI(version=os.getenv('VERSION', 'DEVEL'), name="CalVie", description="Simple iCal viewer", )
templates = Jinja2Templates(directory="templates")


@app.get("/", status_code=status.HTTP_418_IM_A_TEAPOT)
async def root():
    return {"status": "I am a teapot at default"}


async def _run_calendar_job(borrower, slots, url, allow_private_network, payload, cancelled):
    try:
        error = None
        with anyio.CancelScope() as download_scope:
            async def watch_cancel():
                await cancelled.wait()
                download_scope.cancel()
            async with anyio.create_task_group() as watchers:
                watchers.start_soon(watch_cancel)
                try:
                    payload["content"] = await download_calendar(
                        url, allow_private_network=allow_private_network,
                        max_bytes=LIMITS.max_calendar_bytes, timeout=LIMITS.download_timeout)
                except Exception as exc:
                    error = exc
                finally:
                    watchers.cancel_scope.cancel()
        if error is not None:
            raise error
        if cancelled.is_set():
            raise CalendarProcessingCancelled("Calendar request cancelled")
        return await process_calendar(payload, timeout=LIMITS.parser_timeout, memory_mib=LIMITS.parser_memory_mib,
                                      max_response_bytes=LIMITS.max_response_bytes, cancelled=cancelled)
    finally:
        slots.release_on_behalf_of(borrower)


def _calendar_job_finished(job):
    calendar_jobs.discard(job)
    if not job.cancelled():
        # Also consume failures when the requesting client has disconnected.
        job.exception()


async def _calendar_response(name, timezone, days, *, request=None, render_options=None):
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
        start = datetime.now(tz=pytz.UTC)
        end = start.astimezone(tz) + timedelta(days=effective_days)
    except (ValueError, TypeError, OverflowError, pytz.UnknownTimeZoneError):
        raise HTTPException(422, detail="Invalid calendar range") from None
    try:
        if config.name != "DEFAULT":
            allow_private_network = config.getboolean("allow private network", fallback=False)
    except ValueError:
        raise HTTPException(400, detail="Unable to load calendar") from None
    payload = {"start": start.isoformat(), "end": end.isoformat(), "timezone": tz.zone,
               "max_events": LIMITS.max_events, "max_response_bytes": LIMITS.max_response_bytes,
               "format": "json" if render_options is None else "html"}
    if render_options is not None:
        payload.update(render_options)
    cancelled = anyio.Event()
    borrower = object()
    slots = calendar_slots
    try:
        slots.acquire_on_behalf_of_nowait(borrower)
    except anyio.WouldBlock:
        raise HTTPException(503, detail="Calendar service is busy", headers={"Retry-After": "1"}) from None
    try:
        job = asyncio.create_task(_run_calendar_job(borrower, slots, url, allow_private_network, payload, cancelled))
    except BaseException:
        slots.release_on_behalf_of(borrower)
        raise
    calendar_jobs.add(job)
    job.add_done_callback(_calendar_job_finished)

    async def watch_disconnect():
        while True:
            if await request.is_disconnected():
                cancelled.set()
                return
            await anyio.sleep(0.05)

    watcher = asyncio.create_task(watch_disconnect()) if request is not None else None
    try:
        body = await asyncio.shield(job)
    except asyncio.CancelledError:
        cancelled.set()
        raise
    except CalendarDownloadTimeout:
        raise HTTPException(504, detail="Calendar download timed out") from None
    except CalendarProcessingTimeout:
        raise HTTPException(504, detail="Calendar processing timed out") from None
    except CalendarProcessStartError:
        raise HTTPException(500, detail="Unable to start calendar processor") from None
    except Exception:
        raise HTTPException(400, detail="Unable to load calendar") from None
    finally:
        if watcher is not None:
            watcher.cancel()
            with anyio.CancelScope(shield=True):
                await asyncio.gather(watcher, return_exceptions=True)
    return Response(body, media_type="application/json" if render_options is None else "text/html")


@app.get("/cal/{name:path}")
async def cal_data(name: str, request: Request, timezone: str = None, days: int = None):
    return await _calendar_response(name, timezone, days, request=request)


@app.get("/iframe/{name:path}", response_class=HTMLResponse)
async def iframe(request: Request, name: str, timezone: str = None, days: int = None, locale: str = None,
                 width: int = None, colour: Literal["white", "black"] = None,
                 color_scheme: Literal["normal", "light", "dark", "light dark", "dark light"] = None):
    try:
        config = CONFIG[name] if name in CONFIG else CONFIG["DEFAULT"]
        header_language = request.headers.get("accept-languages", "").split(",")[0].replace("-", "_").strip()
        effective_locale = locale or header_language or config["locale"]
        try:
            Locale.parse(effective_locale)
            effective_width = width or config.getint("width")
        except (ValueError, TypeError, UnknownLocaleError):
            raise HTTPException(422, detail="Invalid calendar display options") from None
        options = {"locale": effective_locale, "width": effective_width,
                   "colour": colour, "color_scheme": color_scheme}
        return await _calendar_response(name, timezone, days, request=request, render_options=options)
    except HTTPException as e:
        return templates.TemplateResponse(request=request, name="error.html",
                                          context={"detail": str(e), "status_code": e.status_code},
                                          status_code=e.status_code, headers=e.headers)
