from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from urllib.parse import quote
import yt_dlp
import httpx
import re

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def sanitize_filename(title: str) -> str:
    clean = re.sub(r'[\\/*?:"<>|]', "", title)
    return clean[:50].strip() or "video"

@app.get("/")
def serve_home():
    return FileResponse("index.html")

@app.get("/manifest.json")
def serve_manifest():
    return FileResponse("manifest.json")

YDL_OPTS = {
    'format': 'best[ext=mp4]/best',
    'quiet': True,
    'no_warnings': True,
    'nocheckcertificate': True,
    'geo_bypass': True,
    'extractor_args': {
        'youtube': {
            'player_client': ['ios', 'tv_embedded', 'mweb', 'android']
        }
    },
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1'
    }
}

@app.get("/api/info")
def get_video_info(url: str = Query(...)):
    try:
        with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
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
        with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
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
