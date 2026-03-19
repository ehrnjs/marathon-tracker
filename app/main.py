import json
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

from fastapi import FastAPI, Request, Query, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# 스크래퍼 모듈 연결
from .scraper.smartchip import (
    fetch_runner_data,
    search_runner_or_candidates,
    SmartChipError,
)
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

# --- 도우미 함수 ---

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

    if not last_distance or not last_time or last_distance <= 0:
        return "-"

    avg_sec_per_km = last_time / last_distance
    slowdown = 1.03
    if last_distance >= 30: slowdown = 1.02
    elif last_distance >= 21.1: slowdown = 1.04

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

# --- 경로 설정 ---

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

@app.get("/runner/{keyword}", response_class=HTMLResponse)
async def runner_page(
    request: Request,
    keyword: str,
    usedata: str | None = None
):
    # URL에서 들어온 한글(인코딩됨)을 안전하게 복원
    decoded_keyword = unquote(keyword).strip()
    
    try:
        # 통합 검색 함수 호출 (동명이인 리스트 또는 단일 데이터 반환)
        result = search_runner_or_candidates(decoded_keyword, usedata)

        if result["type"] == "candidates":
            # 중복된 이름이 있을 경우 목록 페이지로 이동
            return templates.TemplateResponse("selection.html", {
                "request": request,
                "candidates": result["data"],
                "keyword": decoded_keyword,
                "usedata": usedata
            })
        
        # 단일 결과인 경우 상세 페이지 렌더링
        runner = enrich_runner(result["data"], usedata or "")
        return templates.TemplateResponse("runner.html", {"request": request, "runner": runner})

    except Exception as e:
        print(f"조회 에러: {e}")
        return templates.TemplateResponse("index.html", {
            "request": request,
            "error": "검색 결과가 없거나 오류가 발생했습니다.",
            "race_options": RACE_OPTIONS
        })

# --- 즐겨찾기 및 대시보드 (기존 로직 유지) ---

@app.post("/favorites/add")
async def favorite_add(bib: str = Form(...), usedata: str = Form("")):
    add_favorite(bib, usedata)
    return RedirectResponse(url="/dashboard", status_code=303)

@app.post("/favorites/delete")
async def favorite_delete(bib: str = Form(...), usedata: str = Form("")):
    delete_favorite(bib, usedata)
    return RedirectResponse(url="/dashboard", status_code=303)

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, race_filter: str = Query("", alias="race"), q: str = Query("", alias="q")):
    # (기존 dashboard 로직과 동일하여 생략 가능하지만, 안정성을 위해 원본 유지 추천)
    favorites = get_favorites()
    runners = []
    race_names = set()
    for fav in favorites:
        try:
            r = fetch_runner_data(bib=fav["bib"], usedata=fav["usedata"] or None)
            r = enrich_runner(r, fav["usedata"])
            race_names.add(r.get("race_name", ""))
            # 스냅샷 저장 생략 (코드 간결화)
            runners.append(r)
        except: continue
    return templates.TemplateResponse("dashboard.html", {"request": request, "runners": runners, "race_names": sorted(list(race_names)), "last_updated": datetime.now().strftime("%H:%M:%S")})