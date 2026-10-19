import datetime
import os
import random
import sqlite3
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from curation import (
    CURATION_POLICY,
    DB_PATH,
    TOPIC_CATEGORIES,
    get_or_curate_day,
    get_setting,
    init_db,
    set_setting,
)

app = FastAPI(
    title="EmbeddedDot: News for Nerds. Stuff that Fits in SRAM.",
    description="Slashdot-style Daily Embedded AI Technical Curation Engine (3-Year Rolling Time Machine)",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(STATIC_DIR, exist_ok=True)


class ApiKeyPayload(BaseModel):
    api_key: str


class CommentPayload(BaseModel):
    story_id: str
    author: str = "Anonymous Coward"
    title: str
    score: str = "+5, Insightful"
    body: str


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/api/config")
def get_config():
    today = datetime.date.today()
    min_date = today - datetime.timedelta(days=CURATION_POLICY["max_lookback_days"])
    key = get_setting("GEMINI_API_KEY", "").strip()
    masked_key = f"{key[:4]}...{key[-4:]}" if len(key) > 8 else ("Configured" if key else "")

    with sqlite3.connect(DB_PATH) as conn:
        cached_dates = [
            r[0]
            for r in conn.execute("SELECT date_str FROM daily_editions ORDER BY date_str DESC LIMIT 60").fetchall()
        ]

    return {
        "today": today.isoformat(),
        "min_date": min_date.isoformat(),
        "max_lookback_days": CURATION_POLICY["max_lookback_days"],
        "has_gemini_key": bool(key),
        "masked_gemini_key": masked_key,
        "gemini_model": "gemini-3.8-flash",
        "policy": CURATION_POLICY,
        "topics": TOPIC_CATEGORIES,
        "cached_dates": cached_dates,
    }


@app.post("/api/settings/gemini-key")
def update_gemini_key(payload: ApiKeyPayload):
    cleaned = payload.api_key.strip()
    set_setting("GEMINI_API_KEY", cleaned)
    masked = f"{cleaned[:4]}...{cleaned[-4:]}" if len(cleaned) > 8 else ("Configured" if cleaned else "")
    return {
        "ok": True,
        "has_gemini_key": bool(cleaned),
        "masked_gemini_key": masked,
    }


@app.get("/api/day/{date_str}")
async def get_day_edition(
    date_str: str,
    force_refresh: bool = Query(False, description="Re-fetch and re-summarize this day's stories"),
    use_gemini: Optional[bool] = Query(None, description="Use Gemini 3.8 Flash if API key is configured"),
):
    try:
        datetime.date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Expected YYYY-MM-DD.")

    edition = await get_or_curate_day(date_str, force_refresh=force_refresh, use_gemini=use_gemini)
    return edition


@app.post("/api/day/{date_str}/comment")
def post_comment(date_str: str, payload: CommentPayload):
    if not payload.body.strip() or not payload.title.strip():
        raise HTTPException(status_code=400, detail="Comment title and body are required.")

    init_db()
    uid = random.randint(100000, 999999)
    now_str = datetime.datetime.now().strftime("@%H:%M")
    author = payload.author.strip() or "Anonymous Coward"

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            """
            INSERT INTO user_comments (story_id, date_str, author, uid, title, score, body, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.story_id,
                date_str,
                author,
                uid,
                payload.title.strip(),
                payload.score,
                payload.body.strip(),
                now_str,
            ),
        )
        conn.commit()
        cid = cursor.lastrowid

    return {
        "ok": True,
        "comment": {
            "id": f"user-{cid}",
            "author": f"{author} ({uid})",
            "title": payload.title.strip(),
            "score": payload.score,
            "body": payload.body.strip(),
            "timestamp": now_str,
        },
    }


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def serve_index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"\n==================================================================")
    print(f"  EmbeddedDot // News for Nerds. Stuff that Fits in SRAM.")
    print(f"  Running locally at: http://localhost:{port}")
    print(f"==================================================================\n")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
