from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from urllib.parse import quote
import yt_dlp
import httpx
import re
import os

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

COOKIE_PATH = "/tmp/cookies.txt"
if os.environ.get("YOUTUBE_COOKIES"):
    with open(COOKIE_PATH, "w", encoding="utf-8") as f:
        f.write(os.environ.get("YOUTUBE_COOKIES"))

def sanitize_filename(title: str) -> str:
    clean = re.sub(r'[\\/*?:"<>|]', "", title)
    return clean[:50].strip() or "video"

@app.get("/")
def serve_home():
    return FileResponse("index.html")

@app.get("/manifest.json")
def serve_manifest():
    return FileResponse("manifest.json")

def get_ydl_opts():
    opts = {
        'format': 'best[ext=mp4]/best',
        'quiet': True,
        'no_warnings': True,
        'nocheckcertificate': True,
        'geo_bypass': True,
    }
    if os.path.exists(COOKIE_PATH):
        opts['cookiefile'] = COOKIE_PATH
    return opts

@app.get("/api/info")
def get_video_info(url: str = Query(...)):
    try:
        with yt_dlp.YoutubeDL(get_ydl_opts()) as ydl:
            info = ydl.extract_info(url, download=False)
            return {
                "title": sanitize_filename(info.get("title", "video")),
                "thumbnail": info.get("thumbnail"),
                "platform": info.get("extractor_key"),
            }
    except Exception as e:
        print("Info Error:", e)
        raise HTTPException(status_code=400, detail="تعذر قراءة الرابط. تأكد أن الفيديو عام وليس خاصاً.")

@app.get("/api/stream")
async def stream_video(url: str = Query(...), title: str = Query("video")):
    try:
        with yt_dlp.YoutubeDL(get_ydl_opts()) as ydl:
            info = ydl.extract_info(url, download=False)
            video_url = info.get("url")
            http_headers = info.get("http_headers", {})

        async def iterfile():
            async with httpx.AsyncClient(headers=http_headers, follow_redirects=True, timeout=60.0) as client:
                async with client.stream("GET", video_url) as r:
                    async for chunk in r.aiter_bytes(chunk_size=1024 * 256):
                        yield chunk

        encoded_filename = quote(f"{sanitize_filename(title)}.mp4")
        return StreamingResponse(
            iterfile(),
            media_type="video/mp4",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"}
        )
    except Exception as e:
        print("Stream Error:", e)
        raise HTTPException(status_code=500, detail="فشل تحميل الفيديو من المصدر.")
