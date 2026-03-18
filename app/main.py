import json
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request, Query, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .scraper.smartchip import fetch_runner_data, SmartChipError
from .database import (
    init_db,
    add_favorite,
    delete_favorite,
    get_favorites,
    get_latest_snapshot,
    save_snapshot,
)

BASE_DIR = Path(__file__).resolve().parent

RACE_OPTIONS = [
    {"label": "2026 서울마라톤", "usedata": "202650000006"}
]

app = FastAPI(title="Marathon Bib Tracker")

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def estimate_finish_time(runner: dict) -> str:
    splits = runner.get("splits", [])
    if not splits:
        return "-"

    last_distance = None
    last_time = None

    for row in reversed(splits):
        point = (row.get("point") or "").lower().replace(" ", "")
        time_str = row.get("time") or ""

        if point in ["finish", "42.2km", "42.195km"]:
            return time_str

        if point.endswith("km"):
            try:
                distance = float(point.replace("km", ""))
                if time_str.count(":") == 2:
                    h, m, s = map(int, time_str.split(":"))
                    elapsed_seconds = h * 3600 + m * 60 + s
                    last_distance = distance
                    last_time = elapsed_seconds
                    break
            except Exception:
                continue

        if point == "21.1km" and time_str.count(":") == 2:
            try:
                h, m, s = map(int, time_str.split(":"))
                elapsed_seconds = h * 3600 + m * 60 + s
                last_distance = 21.1
                last_time = elapsed_seconds
                break
            except Exception:
                continue

    if not last_distance or not last_time or last_distance <= 0:
        return "-"

    avg_sec_per_km = last_time / last_distance

    slowdown = 1.03
    if last_distance >= 30:
        slowdown = 1.02
    elif last_distance >= 21.1:
        slowdown = 1.04

    estimated_total_sec = int(avg_sec_per_km * 42.195 * slowdown)

    h = estimated_total_sec // 3600
    m = (estimated_total_sec % 3600) // 60
    s = estimated_total_sec % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def enrich_runner(runner: dict, usedata: str) -> dict:
    runner["usedata"] = usedata
    runner["estimated_finish"] = estimate_finish_time(runner)

    last_point = "-"
    if runner.get("splits"):
        last_point = runner["splits"][-1].get("point", "-")
    runner["last_point"] = last_point

    return runner


@app.on_event("startup")
def startup():
    init_db()


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "error": None,
            "race_options": RACE_OPTIONS,
        },
    )

@app.get("/runner", response_class=HTMLResponse)
async def runner_page(
    request: Request,
    keyword: str = Query(...),
    usedata: str | None = Query(None),
):
    try:
        runner = fetch_runner_data(bib=keyword, usedata=usedata)
        runner = enrich_runner(runner, usedata or "")
        return templates.TemplateResponse(
            "runner.html",
            {
                "request": request,
                "runner": runner,
            },
        )
    except SmartChipError as e:
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "error": str(e),
                "race_options": RACE_OPTIONS,
            },
            status_code=400,
        )
    except Exception as e:
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "error": f"알 수 없는 오류가 발생했어: {e}",
                "race_options": RACE_OPTIONS,
            },
            status_code=500,
        )

@app.post("/favorites/add")
async def favorite_add(
    bib: str = Form(...),
    usedata: str = Form(""),
):
    add_favorite(bib, usedata)
    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/favorites/delete")
async def favorite_delete(
    bib: str = Form(...),
    usedata: str = Form(""),
):
    delete_favorite(bib, usedata)
    return RedirectResponse(url="/dashboard", status_code=303)


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    race_filter: str = Query("", alias="race"),
    q: str = Query("", alias="q"),
):
    favorites = get_favorites()
    runners = []
    errors = []
    race_names = set()

    for fav in favorites:
        bib = fav.get("bib", "")
        usedata = fav.get("usedata", "") or ""

        try:
            runner = fetch_runner_data(bib=bib, usedata=usedata or None)
            runner = enrich_runner(runner, usedata)

            race_names.add(runner.get("race_name", ""))

            previous = get_latest_snapshot(bib, usedata)
            is_updated = False

            if previous:
                prev_time = previous.get("official_time", "") or ""
                prev_point = previous.get("last_point", "") or ""
                if prev_time != (runner.get("official_time", "") or "") or prev_point != runner["last_point"]:
                    is_updated = True

            runner["is_updated"] = is_updated

            save_snapshot(
                bib=bib,
                usedata=usedata,
                official_time=runner.get("official_time", "") or "",
                last_point=runner["last_point"],
                raw_json=json.dumps(runner, ensure_ascii=False),
            )

            runners.append(runner)

        except Exception as e:
            errors.append(f"{bib} 조회 실패: {e}")

    if race_filter:
        runners = [r for r in runners if (r.get("race_name") or "") == race_filter]

    if q:
        q_lower = q.lower()
        runners = [
            r for r in runners
            if q_lower in (r.get("name", "").lower())
            or q_lower in (r.get("bib", "").lower())
        ]

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "runners": runners,
            "errors": errors,
            "race_names": sorted([r for r in race_names if r]),
            "selected_race": race_filter,
            "query_text": q,
            "last_updated": datetime.now().strftime("%H:%M:%S"),
        },
    )


@app.get("/dashboard/data")
async def dashboard_data(
    race: str = Query(""),
    q: str = Query(""),
):
    favorites = get_favorites()
    runners = []
    errors = []
    race_names = set()

    for fav in favorites:
        bib = fav.get("bib", "")
        usedata = fav.get("usedata", "") or ""

        try:
            runner = fetch_runner_data(bib=bib, usedata=usedata or None)
            runner = enrich_runner(runner, usedata)

            race_names.add(runner.get("race_name", ""))

            previous = get_latest_snapshot(bib, usedata)
            is_updated = False

            if previous:
                prev_time = previous.get("official_time", "") or ""
                prev_point = previous.get("last_point", "") or ""
                if prev_time != (runner.get("official_time", "") or "") or prev_point != runner["last_point"]:
                    is_updated = True

            runner["is_updated"] = is_updated

            save_snapshot(
                bib=bib,
                usedata=usedata,
                official_time=runner.get("official_time", "") or "",
                last_point=runner["last_point"],
                raw_json=json.dumps(runner, ensure_ascii=False),
            )

            runners.append(runner)

        except Exception as e:
            errors.append(f"{bib} 조회 실패: {e}")

    if race:
        runners = [r for r in runners if (r.get("race_name") or "") == race]

    if q:
        q_lower = q.lower()
        runners = [
            r for r in runners
            if q_lower in (r.get("name", "").lower())
            or q_lower in (r.get("bib", "").lower())
        ]

    return JSONResponse(
        {
            "runners": runners,
            "errors": errors,
            "race_names": sorted([r for r in race_names if r]),
            "last_updated": datetime.now().strftime("%H:%M:%S"),
        }
    )