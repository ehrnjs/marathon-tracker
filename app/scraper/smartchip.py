import re
from dataclasses import dataclass, asdict
from typing import Any

import requests
from bs4 import BeautifulSoup


SMARTCHIP_URL = "https://smartchip.co.kr/return_data_livephoto.asp"


class SmartChipError(Exception):
    pass


@dataclass
class SplitRow:
    point: str
    time: str
    time_of_day: str
    pace: str


def decrypt_data(hex_string: str) -> str:
    if not hex_string:
        return ""

    key_array = [1, 4, 11, 14, 0, 9, 8]
    key = "".join(chr(x + 100) for x in key_array)

    result = ""
    for i in range(0, len(hex_string), 4):
        chunk = hex_string[i:i + 4]
        char_code = int(chunk, 16)
        key_char = ord(key[(i // 4) % 7])
        result += chr(char_code ^ key_char)

    return result


def _clean_text(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split()).strip()


def _extract_clock_secret(html: str) -> str | None:
    match = re.search(r'drawTextCanvas\("targetClock",\s*"([0-9A-Fa-f]+)"\)', html)
    return match.group(1) if match else None


def _extract_name_bib_category(soup: BeautifulSoup) -> tuple[str, str, str]:
    container = soup.find("td", class_="recevedata")
    if not container:
        raise SmartChipError("기록 페이지를 찾지 못했어. 배번이나 대회 ID를 확인해줘.")

    text = _clean_text(container.get_text(" ", strip=True))
    bib_match = re.search(r"\bBIB\s+(\S+)", text, re.IGNORECASE)
    bib = bib_match.group(1) if bib_match else ""

    before_bib = text.split("BIB")[0].strip() if "BIB" in text else text
    parts = before_bib.split()
    if len(parts) >= 2:
        category = parts[-1]
        name = " ".join(parts[:-1])
    else:
        category = ""
        name = before_bib

    return name.strip(), bib.strip(), category.strip()


def _extract_race_name(soup: BeautifulSoup) -> str:
    node = soup.select_one(".title-rally")
    return _clean_text(node.get_text()) if node else ""


def _extract_pace_speed(container_text: str) -> tuple[str, str]:
    pace_match = re.search(r"Pace\s+([0-9:]+)\s+min/km", container_text, re.IGNORECASE)
    speed_match = re.search(r"Speed\s+([0-9.]+)\s+km/h", container_text, re.IGNORECASE)
    pace = pace_match.group(1) if pace_match else ""
    speed = speed_match.group(1) if speed_match else ""
    return pace, speed


def _extract_split_columns(soup: BeautifulSoup) -> list[SplitRow]:
    columns = soup.select(".record-flex-wrapper .record-flex-column")
    if len(columns) < 4:
        return []

    decoded_columns: list[list[str]] = []

    for col in columns[:4]:
        cells = col.select(".img-text-cell")
        decoded = []
        for cell in cells:
            secret = cell.get("data-secret", "")
            decoded.append(decrypt_data(secret))
        decoded_columns.append(decoded)

    points = decoded_columns[0]
    times = decoded_columns[1]
    tods = decoded_columns[2]
    paces = decoded_columns[3]

    length = min(len(points), len(times), len(tods), len(paces))
    rows: list[SplitRow] = []

    for i in range(length):
        rows.append(
            SplitRow(
                point=points[i],
                time=times[i],
                time_of_day=tods[i],
                pace=paces[i],
            )
        )

    return rows


def _extract_chart_paces(soup: BeautifulSoup) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    items = soup.select(".chart-label-item")
    for item in items:
        km = item.select_one(".lbl-km")
        pace = item.select_one(".lbl-rank")
        if km and pace:
            result.append(
                {
                    "km": _clean_text(km.get_text()),
                    "pace": _clean_text(pace.get_text()),
                }
            )
    return result


def _build_candidate_from_text(text: str, usedata: str) -> dict[str, str] | None:
    cleaned = _clean_text(text)
    if not cleaned:
        return None

    bib_match = re.search(r"\b(\d{3,})\b", cleaned)
    if not bib_match:
        return None

    bib = bib_match.group(1)
    name = cleaned.replace(bib, "").replace("BIB", "").strip(" -:/")
    if not name:
        name = f"후보 {bib}"

    return {
        "name": name,
        "bib": bib,
        "usedata": usedata,
        "label": cleaned,
    }


def extract_search_candidates(html: str, usedata: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    # 링크/버튼/행 전체에서 bib 후보 찾기
    text_nodes = []

    for tag in soup.find_all(["a", "button", "tr", "li", "td", "div"]):
        text = _clean_text(tag.get_text(" ", strip=True))
        if text:
            text_nodes.append(text)

    for text in text_nodes:
        candidate = _build_candidate_from_text(text, usedata)
        if not candidate:
            continue

        key = (candidate["name"], candidate["bib"])
        if key in seen:
            continue

        # 너무 짧거나 의미 없는 텍스트 제거
        if len(candidate["label"]) < 3:
            continue

        seen.add(key)
        candidates.append(candidate)

    # 이름 검색 결과로 보기 어렵게 너무 많으면 과감히 제한
    return candidates[:30]


def fetch_runner_data(bib: str, usedata: str | None = None) -> dict[str, Any]:
    params = {"nameorBibno": bib}
    if usedata:
        params["usedata"] = usedata

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/127.0.0.0 Safari/537.36"
        )
    }

    response = requests.get(SMARTCHIP_URL, params=params, headers=headers, timeout=20)
    response.raise_for_status()
    html = response.text

    if "스마트칩 마라톤기록조회" not in html:
        raise SmartChipError("스마트칩 응답이 예상과 달라. 잠시 후 다시 시도해줘.")

    soup = BeautifulSoup(html, "html.parser")

    container = soup.find("td", class_="recevedata")
    if not container:
        raise SmartChipError("기록 페이지를 찾지 못했어. 배번이나 대회 ID를 확인해줘.")

    race_name = _extract_race_name(soup)
    name, returned_bib, category = _extract_name_bib_category(soup)

    container_text = _clean_text(container.get_text(" ", strip=True)) if container else ""
    pace, speed = _extract_pace_speed(container_text)

    clock_secret = _extract_clock_secret(html)
    official_time = decrypt_data(clock_secret) if clock_secret else ""

    splits = [asdict(row) for row in _extract_split_columns(soup)]
    chart_paces = _extract_chart_paces(soup)

    if not name and not splits:
        raise SmartChipError("기록을 찾지 못했어. 배번이나 usedata 값을 확인해줘.")

    return {
        "race_name": race_name,
        "name": name,
        "bib": returned_bib or bib,
        "category": category,
        "official_time": official_time,
        "pace": pace,
        "speed": speed,
        "splits": splits,
        "chart_paces": chart_paces,
        "usedata": usedata or "",
    }


def search_runner_or_candidates(keyword: str, usedata: str | None = None) -> dict[str, Any]:
    # 숫자만이면 배번으로 간주 -> 바로 상세 조회
    if keyword.isdigit():
        return {
            "type": "runner",
            "data": fetch_runner_data(keyword, usedata),
        }

    params = {"nameorBibno": keyword}
    if usedata:
        params["usedata"] = usedata

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/127.0.0.0 Safari/537.36"
        )
    }

    response = requests.get(SMARTCHIP_URL, params=params, headers=headers, timeout=20)
    response.raise_for_status()
    html = response.text

    # 상세 페이지면 바로 파싱 시도
    try:
        runner = fetch_runner_data(keyword, usedata)
        return {
            "type": "runner",
            "data": runner,
        }
    except Exception:
        pass

    # 상세가 아니면 후보 목록 추출 시도
    candidates = extract_search_candidates(html, usedata or "")
    if candidates:
        return {
            "type": "candidates",
            "data": candidates,
        }

    raise SmartChipError("이름 검색 결과를 찾지 못했어. 배번으로 검색하거나 이름 표기를 다시 확인해줘.")