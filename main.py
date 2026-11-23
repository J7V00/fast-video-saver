from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from urllib.parse import quote, urlparse, parse_qs
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

def extract_youtube_id(url: str):
    parsed = urlparse(url)
    if "youtu.be" in parsed.netloc:
        return parsed.path.lstrip("/").split("?")[0]
    if "youtube.com" in parsed.netloc:
        if "/shorts/" in parsed.path:
            return parsed.path.split("/shorts/")[1].split("/")[0].split("?")[0]
        if "/watch" in parsed.path:
            return parse_qs(parsed.query).get("v", [None])[0]
    return None

async def fetch_youtube_fallback(video_id: str):
    instances = [
        "https://pipedapi.kavin.rocks",
        "https://pipedapi.adminforge.de",
        "https://api.piped.projectsegfau.lt",
        "https://pipedapi.in.projectsegfau.lt"
    ]
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        for base in instances:
            try:
                res = await client.get(f"{base}/streams/{video_id}")
                if res.status_code == 200:
                    data = res.json()
                    streams = data.get("videoStreams", [])
                    mp4_streams = [s for s in streams if s.get("format") == "MPEG_4" and not s.get("videoOnly")]
                    if not mp4_streams:
                        mp4_streams = [s for s in streams if not s.get("videoOnly")]
                    if mp4_streams:
                        best = sorted(mp4_streams, key=lambda x: int(re.sub(r'\D', '', x.get("quality", "0")) or 0), reverse=True)[0]
                        return {
                            "title": sanitize_filename(data.get("title", "youtube_video")),
                            "thumbnail": data.get("thumbnailUrl"),
                            "platform": "YouTube",
                            "direct_url": best.get("url")
                        }
            except Exception:
                continue
    return None

@app.api_route("/", methods=["GET", "HEAD"])
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
}

@app.get("/api/info")
async def get_video_info(url:
