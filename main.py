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

async def fetch_youtube_via_api(url: str, video_id: str):
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        # 1. محاولة عبر Cobalt API
        cobalt_instances = [
            "https://api.cobalt.tools",
            "https://cobalt-api.kwiatekmiki.com",
            "https://cobalt.api.timelessnesses.me"
        ]
        for api_url in cobalt_instances:
            try:
                res = await client.post(
                    f"{api_url}/",
                    json={"url": url, "videoQuality": "720", "filenameStyle": "basic"},
                    headers={"Accept": "application/json", "Content-Type": "application/json"}
                )
                if res.status_code == 200:
                    data = res.json()
                    if data.get("url"):
                        return {
                            "title": sanitize_filename(data.get("filename", f"youtube_{video_id}").replace(".mp4", "")),
                            "thumbnail": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                            "direct_url": data["url"]
                        }
            except Exception:
                continue

        # 2. محاولة احتياطية عبر Invidious API
        invidious_instances = [
            "https://inv.nadeko.net",
            "https://invidious.nerdvpn.de",
            "https://iv.datura.network"
        ]
        for inv in invidious_instances:
            try:
                res = await client.get(f"{inv}/api/v1/videos/{video_id}")
                if res.status_code == 200:
                    data = res.json()
                    streams = data.get("formatStreams", [])
                    mp4s = [s for s in streams if "video/mp4" in s.get("type", "")]
                    if mp4s:
                        best = mp4s[-1]
                        return {
                            "title": sanitize_filename(data.get("title", f"youtube_{video_id}")),
                            "thumbnail": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                            "direct_url": best["url"]
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
async def get_video_info(url: str = Query(...)):
    yt_id = extract_youtube_id(url)
    if yt_id:
        yt_data = await fetch_youtube_via_api(url, yt_id)
        if yt_data and yt_data.get("direct_url"):
            return {
                "title": yt_data["title"],
                "thumbnail": yt_data["thumbnail"],
                "platform": "YouTube"
            }
        raise HTTPException(status_code=400, detail="يوتيوب يحظر سيرفرات الاستضافة حالياً. جرّب TikTok أو X أو Instagram.")

    try:
        with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
            info = ydl.extract_info(url, download=False)
            return {
                "title": sanitize_filename(info.get("title", "video")),
                "thumbnail": info.get("thumbnail"),
                "platform": info.get("extractor_key"),
            }
    except Exception as e:
        raise HTTPException(status_code=400, detail="تعذر قراءة الرابط. تأكد أن الفيديو عام وليس خاصاً.")

@app.get("/api/stream")
async def stream_video(url: str = Query(...), title: str = Query("video")):
    video_url = None
    http_headers = {}

    yt_id = extract_youtube_id(url)
    if yt_id:
        yt_data = await fetch_youtube_via_api(url, yt_id)
        if yt_data and yt_data.get("direct_url"):
            video_url = yt_data["direct_url"]

    if not video_url:
        try:
            with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
                info = ydl.extract_info(url, download=False)
                video_url = info.get("url")
                http_headers = info.get("http_headers", {})
        except Exception:
            raise HTTPException(status_code=500, detail="فشل تحميل الفيديو من المصدر.")

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
