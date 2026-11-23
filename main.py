from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from urllib.parse import quote, urlparse, parse_qs
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

CF_WORKER_URL = os.environ.get("CF_WORKER_URL", "").rstrip("/")

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

async def fetch_youtube_via_worker(video_id: str):
    if not CF_WORKER_URL:
        return {"error": "CF_WORKER_URL غير مضاف"}

    clients = [
        {
            "context": {
                "client": {
                    "clientName": "ANDROID_VR",
                    "clientVersion": "1.65.10",
                    "deviceMake": "Oculus",
                    "deviceModel": "Quest 3",
                    "osName": "Android",
                    "osVersion": "14",
                    "androidSdkVersion": 34,
                    "hl": "en",
                    "gl": "US"
                }
            },
            "ua": "com.google.android.apps.youtube.vr.oculus/1.65.10 (Linux; U; Android 14; eureka-user Build/UP1A.231005.007) gzip"
        },
        {
            "context": {
                "client": {
                    "clientName": "IOS",
                    "clientVersion": "20.10.4",
                    "deviceMake": "Apple",
                    "deviceModel": "iPhone17,2",
                    "osName": "iPhone",
                    "osVersion": "18.3.1.22D72",
                    "hl": "en",
                    "gl": "US"
                }
            },
            "ua": "com.google.ios.youtube/20.10.4 (iPhone17,2; U; CPU iOS 18_3_1 like Mac OS X;)"
        },
        {
            "context": {
                "client": {
                    "clientName": "ANDROID",
                    "clientVersion": "20.10.38",
                    "deviceMake": "Google",
                    "deviceModel": "Pixel 9 Pro",
                    "osName": "Android",
                    "osVersion": "15",
                    "androidSdkVersion": 35,
                    "hl": "en",
                    "gl": "US"
                }
            },
            "ua": "com.google.android.youtube/20.10.38 (Linux; U; Android 15; en_US; Pixel 9 Pro Build/AP4A.250205.002) gzip"
        }
    ]

    yt_endpoint = quote("https://www.youtube.com/youtubei/v1/player?prettyPrint=false", safe="")
    last_status = "unknown"

    async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
        for _attempt in range(2):
            for c in clients:
                try:
                    payload = {
                        "context": c["context"],
                        "videoId": video_id,
                        "contentCheckOk": True,
                        "racyCheckOk": True
                    }
                    res = await client.post(
                        f"{CF_WORKER_URL}/?url={yt_endpoint}",
                        json=payload,
                        headers={"User-Agent": c["ua"], "Content-Type": "application/json"}
                    )
                    if res.status_code == 200:
                        data = res.json()
                        playability = data.get("playabilityStatus", {})
                        if playability.get("status") != "OK":
                            last_status = f"{playability.get('status')}: {playability.get('reason', '')}"
                            continue

                        streaming = data.get("streamingData", {})
                        formats = streaming.get("formats", []) + streaming.get("adaptiveFormats", [])
                        mp4s = [f for f in formats if f.get("url") and "video/mp4" in f.get("mimeType", "")]
                        if mp4s:
                            combined = [f for f in mp4s if f.get("audioQuality")]
                            pool = combined if combined else mp4s
                            best = sorted(pool, key=lambda x: x.get("height", 0), reverse=True)[0]
                            details = data.get("videoDetails", {})
                            thumbs = details.get("thumbnail", {}).get("thumbnails", [])
                            stream_target = quote(best["url"], safe="")
                            return {
                                "title": sanitize_filename(details.get("title", f"youtube_{video_id}")),
                                "thumbnail": thumbs[-1]["url"] if thumbs else None,
                                "direct_url": f"{CF_WORKER_URL}/?url={stream_target}",
                                "ua": c["ua"]
                            }
                except Exception as e:
                    last_status = str(e)[:80]
                    continue

    return {"error": last_status}

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
        yt_data = await fetch_youtube_via_worker(yt_id)
        if yt_data and yt_data.get("direct_url"):
            return {
                "title": yt_data["title"],
                "thumbnail": yt_data["thumbnail"],
                "platform": "YouTube"
            }
        raise HTTPException(status_code=400, detail=f"رد الجسر: {yt_data.get('error')}")

    try:
        with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
            info = ydl.extract_info(url, download=False)
            return {
                "title": sanitize_filename(info.get("title", "video")),
                "thumbnail": info.get("thumbnail"),
                "platform": info.get("extractor_key"),
            }
    except Exception:
        raise HTTPException(status_code=400, detail="تعذر قراءة الرابط. تأكد أن الفيديو عام وليس خاصاً.")

@app.get("/api/stream")
async def stream_video(url: str = Query(...), title: str = Query("video")):
    video_url = None
    http_headers = {}

    yt_id = extract_youtube_id(url)
    if yt_id:
        yt_data = await fetch_youtube_via_worker(yt_id)
        if yt_data and yt_data.get("direct_url"):
            video_url = yt_data["direct_url"]
            http_headers = {"User-Agent": yt_data["ua"]}
        else:
            raise HTTPException(status_code=500, detail=f"فشل جلب رابط البث من يوتيوب: {yt_data.get('error')}")

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
                if r.status_code not in (200, 206):
                    raise HTTPException(status_code=502, detail=f"رفض خادم الفيديو البث: HTTP {r.status_code}")
                async for chunk in r.aiter_bytes(chunk_size=1024 * 256):
                    yield chunk

    encoded_filename = quote(f"{sanitize_filename(title)}.mp4")
    return StreamingResponse(
        iterfile(),
        media_type="video/mp4",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"}
    )
