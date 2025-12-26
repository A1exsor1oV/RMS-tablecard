import base64
import io
import socket
import subprocess
import platform
import json
import os
import re
import time
from pathlib import Path
from typing import Dict, Literal, Optional, Tuple

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from PIL import Image, ImageDraw, ImageFont

# ---------------- Paths ----------------
BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
DEVICES_PATH = BASE_DIR / "devices.json"

# ---------------- Defaults ----------------
DEFAULT_FONT_PATH = os.getenv(
    "TC_FONT_PATH",
    "C:/Windows/Fonts/arialbd.ttf" if os.name == "nt" else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)

SIZE_PROFILES = {
    "nameplate7":  {"w": 1024, "h": 600, "max_kb": 250},
    "nameplate10": {"w": 1600, "h": 720, "max_kb": 600},
}

# ---------------- Runtime config ----------------
_gateway = {"host": os.getenv("TC_GATEWAY_HOST", "127.0.0.1"),
            "port": int(os.getenv("TC_GATEWAY_PORT", "5003"))}

def load_gateway_from_file():
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            host = str(data.get("gateway_host", _gateway["host"]))
            port = int(data.get("gateway_port", _gateway["port"]))
            _gateway["host"] = host
            _gateway["port"] = port
        except Exception:
            pass

def save_gateway_to_file():
    CONFIG_PATH.write_text(json.dumps({
        "gateway_host": _gateway["host"],
        "gateway_port": _gateway["port"],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

def gateway_base() -> str:
    return f"http://{_gateway['host']}:{_gateway['port']}"

load_gateway_from_file()

# ---------------- Helpers ----------------
def now_ms() -> int:
    return int(time.time() * 1000)

def next_seq() -> int:
    return int(time.time() * 1000) % 2_000_000_000

def norm_mac(mac: str) -> str:
    return re.sub(r"[^0-9a-fA-F]", "", mac or "").lower()

def _hex_to_rgb(h: str) -> Tuple[int, int, int]:
    h = (h or "").strip()
    if not h.startswith("#") or len(h) != 7:
        raise ValueError("Expected HEX color like #RRGGBB")
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)

def _load_font(size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(DEFAULT_FONT_PATH, size=size)
    except Exception:
        return ImageFont.load_default()

def _load_devices_json() -> Dict:
    if not DEVICES_PATH.exists():
        return {"devices": []}
    try:
        return json.loads(DEVICES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"devices": []}

def gateway_post(path: str, payload: dict, timeout: float = 6.0) -> dict:
    url = f"{gateway_base()}{path}"
    try:
        r = requests.post(url, json=payload, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Gateway request failed: {e}")

def _image_from_base64(b64: str) -> Image.Image:
    """
    b64 может приходить как чистый base64, либо data URL: data:image/png;base64,...
    """
    if not b64:
        raise ValueError("empty base64")
    if b64.startswith("data:"):
        b64 = b64.split(",", 1)[1]
    raw = base64.b64decode(b64)
    img = Image.open(io.BytesIO(raw))
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA")
    return img

def _draw_bg_cover(dst: Image.Image, bg: Image.Image) -> None:
    w, h = dst.size
    bg = bg.convert("RGBA")

    bw, bh = bg.size
    dst_ratio = w / h
    bg_ratio = bw / bh

    if bg_ratio > dst_ratio:
        new_w = int(bh * dst_ratio)
        x0 = (bw - new_w) // 2
        crop = bg.crop((x0, 0, x0 + new_w, bh))
    else:
        new_h = int(bw / dst_ratio)
        y0 = (bh - new_h) // 2
        crop = bg.crop((0, y0, bw, y0 + new_h))

    crop = crop.resize((w, h), Image.LANCZOS)
    dst.paste(crop, (0, 0), crop)

def render_to_jpg(req) -> Tuple[bytes, int, int]:
    prof = SIZE_PROFILES[req.profile]
    w, h, max_kb = prof["w"], prof["h"], prof["max_kb"]

    base = Image.new("RGBA", (w, h), (0, 0, 0, 255))

    if req.background_image_base64:
        try:
            bg = _image_from_base64(req.background_image_base64)
            _draw_bg_cover(base, bg)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid background image base64: {e}")
    else:
        try:
            base = Image.new("RGBA", (w, h), (*_hex_to_rgb(req.background), 255))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid background color: {e}")

    draw = ImageDraw.Draw(base)

    tb = req.text
    font = _load_font(tb.size)

    lines = (tb.text or "").split("\n")

    line_heights, line_widths = [], []
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_widths.append(bbox[2] - bbox[0])
        line_heights.append(bbox[3] - bbox[1])

    block_w = max(line_widths) if line_widths else 0
    block_h = sum(line_heights) + tb.line_spacing * max(0, len(lines) - 1)

    #x
    if tb.align == "left":
        x0 = tb.margin_x
    elif tb.align == "right":
        x0 = w - tb.margin_x - block_w
    else:
        x0 = (w - block_w) // 2

    # y
    if tb.valign == "top":
        y0 = tb.margin_y
    elif tb.valign == "bottom":
        y0 = h - tb.margin_y - block_h
    else:
        y0 = (h - block_h) // 2

    # draw text
    y = y0
    fill = _hex_to_rgb(tb.color)
    for i, line in enumerate(lines):
        lw = line_widths[i] if i < len(line_widths) else 0
        if tb.align == "left":
            x = tb.margin_x
        elif tb.align == "right":
            x = w - tb.margin_x - lw
        else:
            x = (w - lw) // 2

        draw.text((x, y), line, fill=fill, font=font)
        y += (line_heights[i] if i < len(line_heights) else 0) + tb.line_spacing

    # JPEG encode
    rgb = base.convert("RGB")
    q = 90
    best = None
    while q >= 30:
        bio = io.BytesIO()
        rgb.save(bio, format="JPEG", quality=q, optimize=True, progressive=True)
        data = bio.getvalue()
        if len(data) <= max_kb * 1024:
            best = data
            break
        q -= 5

    if best is None:
        bio = io.BytesIO()
        rgb.save(bio, format="JPEG", quality=30, optimize=True, progressive=True)
        best = bio.getvalue()

    return best, w, h

def tcp_probe(ip: str, ports=(80, 443, 8080), timeout=0.35) -> bool:
    if not ip:
        return False
    for p in ports:
        try:
            with socket.create_connection((ip, p), timeout=timeout):
                return True
        except OSError:
            continue
    return False

def ping_probe(ip: str, timeout_ms: int = 600) -> bool:
    if not ip:
        return False

    system = platform.system().lower()

    if system == "windows":
        # -n 1 = один пакет, -w timeout в мс
        cmd = ["ping", "-n", "1", "-w", str(timeout_ms), ip]
    else:
        # -c 1 = один пакет, -W timeout в сек (целое)
        cmd = ["ping", "-c", "1", "-W", str(max(1, timeout_ms // 1000)), ip]

    try:
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return r.returncode == 0
    except Exception:
        return False

# ---------------- API Models ----------------
class TextBlock(BaseModel):
    text: str = Field(..., min_length=1, max_length=200)
    color: str = Field("#000000")
    size: int = Field(64, ge=10, le=220)
    align: Literal["left", "center", "right"] = "center"
    valign: Literal["top", "center", "bottom"] = "center"
    margin_x: int = Field(60, ge=0, le=400)
    margin_y: int = Field(40, ge=0, le=300)
    line_spacing: int = Field(10, ge=0, le=60)

class RenderRequest(BaseModel):
    profile: Literal["nameplate7", "nameplate10"] = "nameplate10"
    background: str = Field("#FFFFFF", description="HEX #RRGGBB")
    background_image_base64: Optional[str] = Field(
        None, description="Опционально. Base64 картинки или data URL data:image/...;base64,..."
    )
    text: TextBlock

class RenderResponse(BaseModel):
    profile: str
    width: int
    height: int
    jpg_bytes: int
    jpg_base64: str

class PushRequest(BaseModel):
    mac: str
    render: RenderRequest

class PushResponse(BaseModel):
    mac: str
    jpg_bytes: int
    sent_seq: int
    gateway_send: dict

class PushStatusRequest(BaseModel):
    mac: str
    response_seq: int

class PushStatusResponse(BaseModel):
    mac: str
    status: int
    response_seq: int
    raw: dict

class GatewaySetRequest(BaseModel):
    gateway_host: str
    gateway_port: int = Field(5003, ge=1, le=65535)

class MacRequest(BaseModel):
    mac: str

# ---------------- FastAPI ----------------
app = FastAPI(
    title="TableCard Control Microservice",
    version="0.2.0",
    description="GUI + API: фон/текст -> JPEG -> TableCard Gateway",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static UI
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

@app.get("/")
def ui():
    return FileResponse(str(BASE_DIR / "static" / "index.html"))

@app.get("/health")
def health():
    return {"status": "ok", "gateway": gateway_base()}

# ---------------- Gateway config endpoints ----------------
@app.get("/gateway")
def gateway_get():
    return {"gateway_host": _gateway["host"], "gateway_port": _gateway["port"]}

@app.post("/gateway/set")
def gateway_set(req: GatewaySetRequest):
    _gateway["host"] = req.gateway_host.strip()
    _gateway["port"] = int(req.gateway_port)
    save_gateway_to_file()
    return {"status": "ok", "gateway": gateway_base()}

# ---------------- Device list ----------------
@app.get("/devices/list")
def devices_list():
    cfg = _load_devices_json()
    allowed = cfg.get("devices", [])

    try:
        payload = {"seq": 1, "type": "get_dev_list", "timestamp": now_ms()}
        data = gateway_post("/device/getDeviceList", payload)
        gw_devices = data.get("message", {}).get("data", []) or []
    except Exception as e:
        return {
            "devices": [],
            "gateway": gateway_base(),
            "gateway_reachable": False,
            "error": str(e),
        }

    gw_by_mac = {norm_mac(d.get("mac", "")): d for d in gw_devices}

    out = []
    for d in allowed:
        mac = norm_mac(d.get("mac", ""))
        if not mac:
            continue

        gw = gw_by_mac.get(mac)
        if not gw:
            continue

        status_local = d.get("status", "base")
        if status_local != "base":
            continue

        ip = gw.get("IPAdd", "")
        st_gw = (gw.get("Status", "") or "").lower()
        online = ping_probe(ip)

        if not online:
            continue

        out.append({
            "mac": mac,
            "name": d.get("name", mac),
            "profile": d.get("profile", "nameplate10"),
            "ip": ip,
            # статусы можно оставить для диагностики
            "status_gateway": st_gw,
            "status_local": status_local,
        })

    return {"devices": out, "gateway": gateway_base(), "gateway_reachable": True}

@app.post("/devices/sync")
def devices_sync():
    payload = {"seq": 1, "type": "get_dev_list", "timestamp": now_ms()}
    return gateway_post("/device/getDeviceList", payload)

@app.post("/render", response_model=RenderResponse)
def render(req: RenderRequest):
    jpg, w, h = render_to_jpg(req)
    return RenderResponse(
        profile=req.profile,
        width=w,
        height=h,
        jpg_bytes=len(jpg),
        jpg_base64=base64.b64encode(jpg).decode("ascii"),
    )

@app.post("/push", response_model=PushResponse)
def push(req: PushRequest):
    mac = norm_mac(req.mac)

    jpg, _, _ = render_to_jpg(req.render)
    img_b64 = base64.b64encode(jpg).decode("ascii")

    seq = next_seq()
    payload = {
        "seq": seq,
        "timestamp": now_ms(),
        "type": "update_image",
        "mac": mac,
        "image": img_b64,
    }
    gw = gateway_post("/device/updateDevImg", payload)
    return PushResponse(mac=mac, jpg_bytes=len(jpg), sent_seq=seq, gateway_send=gw)

@app.post("/push/status", response_model=PushStatusResponse)
def push_status(req: PushStatusRequest):
    mac = norm_mac(req.mac)
    payload = {"seq": 1, "type": "update_dev_status", "timestamp": now_ms(), "mac": mac}
    gw = gateway_post("/device/updateDevImgStatus", payload)

    try:
        status = int(gw["data"]["data"]["status"])
        response_seq = int(gw["data"]["response_seq"])
    except Exception:
        raise HTTPException(status_code=502, detail=f"Unexpected gateway response: {gw}")

    return PushStatusResponse(mac=mac, status=status, response_seq=response_seq, raw=gw)

@app.post("/devices/mark_filled")
def mark_filled(req: MacRequest):
    mac = norm_mac(req.mac)
    cfg = _load_devices_json()
    found = False

    for d in cfg.get("devices", []):
        if norm_mac(d.get("mac", "")) == mac:
            d["status"] = "filled"
            found = True
            break

    if not found:
        raise HTTPException(status_code=404, detail="MAC not found in devices.json")

    DEVICES_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "ok", "mac": mac}
    
@app.post("/devices/refresh")
def refresh_devices():
    cfg = _load_devices_json()
    for d in cfg.get("devices", []):
        d["status"] = "base"

    DEVICES_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "ok", "count": len(cfg.get("devices", []))}



