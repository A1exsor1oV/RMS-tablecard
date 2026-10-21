import asyncio
import base64
import hashlib
from contextlib import asynccontextmanager
import io
import logging
import socket
import subprocess
import platform
import json
import os
import re
import stat
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from PIL import Image, ImageDraw, ImageFont

# ---------------- Paths ----------------
BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
DEVICES_PATH = BASE_DIR / "devices.json"
DISCOVERY_INTERVAL_SECONDS = 15
_devices_lock = threading.RLock()
_discovery_lock = threading.Lock()
logger = logging.getLogger(__name__)

# ---------------- Defaults ----------------
DEFAULT_FONT_PATH = os.getenv(
    "TC_FONT_PATH",
    "C:/Windows/Fonts/arialbd.ttf" if os.name == "nt" else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)

# File names are server-owned: API callers select a family, never a file path.
# Tuple order: regular, bold, italic, bold italic.
FONT_FAMILIES = {
    "arial": ("Arial", "C:/Windows/Fonts", ("arial.ttf", "arialbd.ttf", "ariali.ttf", "arialbi.ttf")),
    "times": ("Times New Roman", "C:/Windows/Fonts", ("times.ttf", "timesbd.ttf", "timesi.ttf", "timesbi.ttf")),
    "georgia": ("Georgia", "C:/Windows/Fonts", ("georgia.ttf", "georgiab.ttf", "georgiai.ttf", "georgiaz.ttf")),
    "verdana": ("Verdana", "C:/Windows/Fonts", ("verdana.ttf", "verdanab.ttf", "verdanai.ttf", "verdanaz.ttf")),
    "courier": ("Courier New", "C:/Windows/Fonts", ("cour.ttf", "courbd.ttf", "couri.ttf", "courbi.ttf")),
    "trebuchet": ("Trebuchet MS", "C:/Windows/Fonts", ("trebuc.ttf", "trebucbd.ttf", "trebucit.ttf", "trebucbi.ttf")),
    "dejavu_sans": ("DejaVu Sans", "/usr/share/fonts/truetype/dejavu", ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans-Oblique.ttf", "DejaVuSans-BoldOblique.ttf")),
    "dejavu_serif": ("DejaVu Serif", "/usr/share/fonts/truetype/dejavu", ("DejaVuSerif.ttf", "DejaVuSerif-Bold.ttf", "DejaVuSerif-Italic.ttf", "DejaVuSerif-BoldItalic.ttf")),
    "dejavu_mono": ("DejaVu Sans Mono", "/usr/share/fonts/truetype/dejavu", ("DejaVuSansMono.ttf", "DejaVuSansMono-Bold.ttf", "DejaVuSansMono-Oblique.ttf", "DejaVuSansMono-BoldOblique.ttf")),
    "liberation_sans": ("Liberation Sans", "/usr/share/fonts/truetype/liberation2", ("LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf", "LiberationSans-Italic.ttf", "LiberationSans-BoldItalic.ttf")),
    "liberation_serif": ("Liberation Serif", "/usr/share/fonts/truetype/liberation2", ("LiberationSerif-Regular.ttf", "LiberationSerif-Bold.ttf", "LiberationSerif-Italic.ttf", "LiberationSerif-BoldItalic.ttf")),
    "liberation_mono": ("Liberation Mono", "/usr/share/fonts/truetype/liberation2", ("LiberationMono-Regular.ttf", "LiberationMono-Bold.ttf", "LiberationMono-Italic.ttf", "LiberationMono-BoldItalic.ttf")),
}

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

def device_mac(value) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F:\-.\s]+", value):
        return ""
    mac = norm_mac(value)
    return mac if len(mac) == 12 else ""

def _hex_to_rgb(h: str) -> Tuple[int, int, int]:
    h = (h or "").strip()
    if not h.startswith("#") or len(h) != 7:
        raise ValueError("Expected HEX color like #RRGGBB")
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)

def font_files(family: str) -> Tuple[Path, ...]:
    if family == "default":
        if os.getenv("TC_FONT_PATH"):
            return (Path(DEFAULT_FONT_PATH),) * 4
        family = "arial" if os.name == "nt" else "dejavu_sans"
    entry = FONT_FAMILIES.get(family)
    if entry is None:
        raise HTTPException(status_code=422, detail="Неизвестный шрифт. Выберите шрифт из списка.")
    _, directory, files = entry
    return tuple(Path(directory) / filename for filename in files)

def load_text_font(family: str, size: int, bold: bool = False, italic: bool = False) -> ImageFont.FreeTypeFont:
    path = font_files(family)[int(bold) + 2 * int(italic)]
    try:
        return ImageFont.truetype(str(path), size=size)
    except OSError as exc:
        raise HTTPException(status_code=422, detail="Выбранный шрифт недоступен на сервере. Выберите другой шрифт.") from exc

def available_fonts() -> List[dict]:
    result = []
    for family in ("default", *FONT_FAMILIES):
        try:
            for path in font_files(family):
                ImageFont.truetype(str(path), size=16)
        except OSError:
            continue
        if family == "default":
            label = Path(DEFAULT_FONT_PATH).stem if os.getenv("TC_FONT_PATH") else ("Arial" if os.name == "nt" else "DejaVu Sans")
            label = f"По умолчанию ({label})"
        else:
            label = FONT_FAMILIES[family][0]
        result.append({"id": family, "name": label})
    return result

def _load_devices_json() -> Dict:
    with _devices_lock:
        if not DEVICES_PATH.exists():
            return {"devices": []}
        try:
            cfg = json.loads(DEVICES_PATH.read_text(encoding="utf-8"))
            if not isinstance(cfg, dict) or not isinstance(cfg.get("devices"), list):
                raise ValueError("ожидается объект с массивом devices")
            if any(not isinstance(d, dict) for d in cfg["devices"]):
                raise ValueError("каждая табличка должна быть объектом")
            return cfg
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail=f"Не удалось прочитать devices.json: {exc}") from exc

def _save_devices_json(cfg: dict) -> None:
    # Replace only a complete file; all read/modify/write operations hold this lock.
    with _devices_lock:
        temp_path = None
        try:
            mode = stat.S_IMODE(DEVICES_PATH.stat().st_mode) if DEVICES_PATH.exists() else 0o644
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=DEVICES_PATH.parent,
                prefix=".devices-", suffix=".tmp", delete=False,
            ) as output:
                temp_path = Path(output.name)
                json.dump(cfg, output, ensure_ascii=False, indent=2)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temp_path, mode)
            os.replace(temp_path, DEVICES_PATH)
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()

def gateway_post(path: str, payload: dict, timeout: float = 6.0) -> dict:
    url = f"{gateway_base()}{path}"
    try:
        r = requests.post(url, json=payload, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Gateway request failed: {e}")

def gateway_devices() -> List[dict]:
    data = gateway_post("/device/getDeviceList", {"seq": 1, "type": "get_dev_list", "timestamp": now_ms()})
    message = data.get("message") if isinstance(data, dict) else None
    devices = message.get("data") if isinstance(message, dict) else None
    if not isinstance(devices, list):
        raise HTTPException(status_code=502, detail="Шлюз вернул некорректный список табличек.")
    return [d for d in devices if isinstance(d, dict) and device_mac(d.get("mac"))]

def gateway_device_is_online(device: dict) -> bool:
    # The gateway retains offline records whose former IP may belong to another MAC.
    # A successful ping to that IP cannot establish that this device is online.
    return str(device.get("Status", "")).strip().lower() == "online"

def discover_devices() -> dict:
    # Serialize manual and scheduled scans, without locking the file during HTTP I/O.
    with _discovery_lock:
        found = gateway_devices()
        with _devices_lock:
            cfg = _load_devices_json()
            known = {device_mac(d.get("mac")) for d in cfg["devices"]}
            added = []
            for device in found:
                if not gateway_device_is_online(device):
                    continue
                mac = device_mac(device["mac"])
                if mac in known:
                    continue
                cfg["devices"].append({"mac": mac, "name": "", "profile": "nameplate7", "status": "base"})
                known.add(mac)
                added.append(mac)
            if added:
                _save_devices_json(cfg)
            return {"status": "ok", "added": len(added), "macs": added, "total": len(cfg["devices"])}

def discovery_loop(stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            result = discover_devices()
            if result["added"]:
                logger.info("Discovered %s new table cards", result["added"])
        except HTTPException as exc:
            logger.warning("Table card discovery: %s", exc.detail)
        except Exception:
            logger.exception("Table card discovery failed")
        stop.wait(DISCOVERY_INTERVAL_SECONDS)

@asynccontextmanager
async def lifespan(app: FastAPI):
    stop = threading.Event()
    worker = threading.Thread(target=discovery_loop, args=(stop,), name="tablecard-discovery", daemon=True)
    worker.start()
    try:
        yield
    finally:
        stop.set()
        await asyncio.to_thread(worker.join)

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

    def _wrap(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> List[str]:
        text = (text or "").strip()
        if not text:
            return [" "]
        words = re.split(r"\s+", text)
        lines: List[str] = []
        line = ""
        for w_ in words:
            cand = (line + " " + w_).strip() if line else w_
            bbox = draw.textbbox((0, 0), cand, font=font)
            if (bbox[2] - bbox[0]) <= max_width or not line:
                line = cand
            else:
                lines.append(line)
                line = w_
        if line:
            lines.append(line)
        return lines

    def _wrap_fio_smart(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> List[str]:
        text = (text or "").strip()
        if not text:
            return [" "]

        parts = re.split(r"\s+", text)
        one = " ".join(parts)
        bbox = draw.textbbox((0, 0), one, font=font)
        if (bbox[2] - bbox[0]) <= max_width:
            return [one]

        if len(parts) >= 3:
            line1 = parts[0]
            rest = " ".join(parts[1:])
            rest_lines = _wrap(rest, font, max_width)
            return [line1] + rest_lines

        return _wrap(one, font, max_width)

    def _anchor(position: str) -> Tuple[float, float]:
        mp = {
            "top-left": (0.0, 0.0),
            "top": (0.5, 0.0),
            "top-right": (1.0, 0.0),
            "left": (0.0, 0.5),
            "center": (0.5, 0.5),
            "right": (1.0, 0.5),
            "bottom-left": (0.0, 1.0),
            "bottom": (0.5, 1.0),
            "bottom-right": (1.0, 1.0),
        }
        return mp.get(position or "center", (0.5, 0.5))

    def _align_from_position(position: str) -> Literal["left", "center", "right"]:
        if "left" in (position or ""):
            return "left"
        if "right" in (position or ""):
            return "right"
        return "center"

    fio = req.text.fio
    title = req.text.title

    margin = fio.margin
    max_width = max(10, w - margin * 2)

    fio_font = load_text_font(fio.font_family, fio.size, fio.bold, fio.italic)
    fio_lines = _wrap_fio_smart(fio.value, fio_font, max_width)

    title_lines: List[str] = []
    title_font = None
    if title and (title.value or "").strip():
        title_font = load_text_font(title.font_family, title.size)
        title_lines = _wrap(title.value, title_font, max_width)

    fio_line_h = int(fio.size * 1.15)
    title_line_h = int((title.size if title else 0) * 1.15) if title_lines else 0

    block_h = len(fio_lines) * fio_line_h
    if title_lines:
        block_h += title.offset_y + len(title_lines) * title_line_h

    # width is max of line widths
    block_w = 0
    for ln in fio_lines:
        bbox = draw.textbbox((0, 0), ln, font=fio_font)
        block_w = max(block_w, bbox[2] - bbox[0])
    if title_lines and title_font:
        for ln in title_lines:
            bbox = draw.textbbox((0, 0), ln, font=title_font)
            block_w = max(block_w, bbox[2] - bbox[0])

    ax, ay = _anchor(fio.position)
    area_w = w - margin * 2
    area_h = h - margin * 2
    x0 = int(margin + (area_w - block_w) * ax)
    y0 = int(margin + (area_h - block_h) * ay)

    align = _align_from_position(fio.position)

    def _x_for_align(base_x: int, width_: int) -> int:
        if align == "left":
            return base_x
        if align == "right":
            return base_x + width_
        return base_x + width_ // 2

    x_text = _x_for_align(x0, block_w)

    # draw fio
    y = y0
    for ln in fio_lines:
        bbox = draw.textbbox((0, 0), ln, font=fio_font)
        ln_w = bbox[2] - bbox[0]
        if align == "left":
            lx = x_text
        elif align == "right":
            lx = x_text - ln_w
        else:
            lx = x_text - ln_w // 2
        draw.text((lx, y), ln, font=fio_font, fill=fio.color)

        if fio.underline:
            uy = y + fio.size + max(4, fio.size // 18)
            draw.line((lx, uy, lx + ln_w, uy), fill=fio.color, width=max(2, fio.size // 40))

        y += fio_line_h + fio.line_spacing

    # draw title
    if title_lines and title_font and title:
        y += title.offset_y
        for ln in title_lines:
            bbox = draw.textbbox((0, 0), ln, font=title_font)
            ln_w = bbox[2] - bbox[0]
            if align == "left":
                lx = x_text
            elif align == "right":
                lx = x_text - ln_w
            else:
                lx = x_text - ln_w // 2
            draw.text((lx, y), ln, font=title_font, fill=title.color)
            y += title_line_h + title.line_spacing

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
class TextFio(BaseModel):
    value: str = Field(..., min_length=1, max_length=220)
    font_family: str = Field("default", max_length=64)
    color: str = Field("#000000", description="HEX #RRGGBB")
    size: int = Field(96, ge=10, le=220)
    bold: bool = False
    italic: bool = False
    underline: bool = False
    position: Literal[
        "center",
        "top",
        "bottom",
        "left",
        "right",
        "top-left",
        "top-right",
        "bottom-left",
        "bottom-right",
    ] = "center"
    margin: int = Field(48, ge=0, le=240)
    line_spacing: int = Field(10, ge=0, le=80)

class TextTitle(BaseModel):
    value: str = Field("", max_length=220)
    font_family: str = Field("default", max_length=64)
    color: str = Field("#111827", description="HEX #RRGGBB")
    size: int = Field(64, ge=10, le=220)
    offset_y: int = Field(18, ge=0, le=140)
    line_spacing: int = Field(8, ge=0, le=80)

class TextLayout(BaseModel):
    fio: TextFio
    title: Optional[TextTitle] = None

class RenderRequest(BaseModel):
    profile: Literal["nameplate7", "nameplate10"] = "nameplate7"
    background: str = Field("#FFFFFF", description="HEX #RRGGBB")
    background_image_base64: Optional[str] = Field(
        None, description="Опционально. Base64 картинки или data URL data:image/...;base64,..."
    )
    text: TextLayout

class RenderResponse(BaseModel):
    profile: str
    width: int
    height: int
    jpg_bytes: int
    jpg_base64: str
    jpg_sha256: str

class PushRequest(BaseModel):
    mac: str
    render: RenderRequest
    expected_jpg_sha256: Optional[str] = Field(None, pattern=r"^[0-9a-f]{64}$")

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

class ConfigUpdate(BaseModel):
    gateway_host: str
    gateway_port: int = Field(5003, ge=1, le=65535)

# ---------------- FastAPI ----------------
app = FastAPI(
    title="TableCard Control Microservice",
    version="0.2.0",
    description="GUI + API: фон/текст -> JPEG -> TableCard Gateway",
    lifespan=lifespan,
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

@app.get("/fonts")
def fonts_list():
    return {"fonts": available_fonts(), "default": "default"}

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

@app.get("/config")
def config_get():
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {"gateway_host": _gateway["host"], "gateway_port": _gateway["port"]}
    return {"gateway_host": _gateway["host"], "gateway_port": _gateway["port"]}

@app.post("/config")
def config_set(req: ConfigUpdate):
    _gateway["host"] = req.gateway_host.strip()
    _gateway["port"] = int(req.gateway_port)

    save_gateway_to_file()

    return {"status": "ok", "config": {"gateway_host": _gateway["host"], "gateway_port": _gateway["port"]}}
# ---------------- Device list ----------------
@app.get("/devices/list")
def devices_list():
    allowed = _load_devices_json()["devices"]
    try:
        gw_devices = gateway_devices()
    except Exception as e:
        return {
            "devices": [],
            "gateway": gateway_base(),
            "gateway_reachable": False,
            "error": str(e),
            "counts": {"registered": len(allowed)},
        }

    gw_by_mac = {device_mac(d.get("mac")): d for d in gw_devices if gateway_device_is_online(d)}

    out = []
    counts = {"registered": len(allowed), "filled": 0, "offline": 0, "unreachable": 0}
    for d in allowed:
        mac = norm_mac(d.get("mac", ""))
        if not mac:
            continue

        status_local = d.get("status", "base")
        if status_local != "base":
            counts["filled"] += 1
            continue

        gw = gw_by_mac.get(mac)
        if not gw:
            counts["offline"] += 1
            continue

        ip = gw.get("IPAdd", "")
        st_gw = str(gw.get("Status", "")).strip().lower()
        online = ping_probe(ip)

        if not online:
            counts["unreachable"] += 1
            continue

        out.append({
            "mac": mac,
            "name": d.get("name", mac),
            "profile": d.get("profile", "nameplate7"),
            "ip": ip,
            # статусы можно оставить для диагностики
            "status_gateway": st_gw,
            "status_local": status_local,
        })

    return {"devices": out, "gateway": gateway_base(), "gateway_reachable": True, "counts": counts}

@app.post("/devices/sync")
def devices_sync():
    payload = {"seq": 1, "type": "get_dev_list", "timestamp": now_ms()}
    return gateway_post("/device/getDeviceList", payload)

@app.post("/devices/discover")
def devices_discover():
    return discover_devices()

@app.post("/render", response_model=RenderResponse)
def render(req: RenderRequest):
    jpg, w, h = render_to_jpg(req)
    return RenderResponse(
        profile=req.profile,
        width=w,
        height=h,
        jpg_bytes=len(jpg),
        jpg_base64=base64.b64encode(jpg).decode("ascii"),
        jpg_sha256=hashlib.sha256(jpg).hexdigest(),
    )

@app.post("/push", response_model=PushResponse)
def push(req: PushRequest):
    mac = norm_mac(req.mac)

    jpg, _, _ = render_to_jpg(req.render)
    if req.expected_jpg_sha256 is not None and hashlib.sha256(jpg).hexdigest() != req.expected_jpg_sha256:
        raise HTTPException(status_code=409, detail="Изображение изменилось. Обновите предпросмотр перед отправкой.")
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
    with _devices_lock:
        cfg = _load_devices_json()
        for d in cfg["devices"]:
            if norm_mac(d.get("mac", "")) == mac:
                if d.get("status") != "filled":
                    d["status"] = "filled"
                    _save_devices_json(cfg)
                return {"status": "ok", "mac": mac}
        raise HTTPException(status_code=404, detail="MAC not found in devices.json")
    
@app.post("/devices/refresh")
def refresh_devices():
    with _devices_lock:
        cfg = _load_devices_json()
        for d in cfg["devices"]:
            d["status"] = "base"
        _save_devices_json(cfg)
        return {"status": "ok", "count": len(cfg["devices"])}

class DeviceRecord(BaseModel):
    mac: str
    name: str = ""
    profile: Literal["nameplate7", "nameplate10"] = "nameplate7"
    status: Literal["base", "filled"] = "base"

    @field_validator("mac")
    @classmethod
    def valid_mac(cls, value: str) -> str:
        mac = device_mac(value)
        if not mac:
            raise ValueError("MAC должен содержать 12 шестнадцатеричных цифр")
        return mac

class DevicesFile(BaseModel):
    devices: List[DeviceRecord]
    base_devices: List[DeviceRecord]

    @field_validator("devices", "base_devices")
    @classmethod
    def unique_macs(cls, devices):
        if len({d.mac for d in devices}) != len(devices):
            raise ValueError("MAC-адреса табличек не должны повторяться")
        return devices

class DeviceNameEdit(BaseModel):
    model_config = {"extra": "forbid"}
    mac: str
    name: str = Field(max_length=120)
    base_name: str

    @field_validator("mac")
    @classmethod
    def valid_mac(cls, value: str) -> str:
        mac = device_mac(value)
        if not mac:
            raise ValueError("MAC должен содержать 12 шестнадцатеричных цифр")
        return mac

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        return value.strip()

class DeviceNamesUpdate(BaseModel):
    model_config = {"extra": "forbid"}
    changes: List[DeviceNameEdit]

    @field_validator("changes")
    @classmethod
    def unique_macs(cls, changes):
        if len({d.mac for d in changes}) != len(changes):
            raise ValueError("MAC-адреса табличек не должны повторяться")
        return changes

def device_names(cfg: dict) -> List[dict]:
    return [{"mac": device_mac(d.get("mac")), "name": d.get("name", "")} for d in cfg["devices"]]

@app.get("/devices/names")
def devices_names_get():
    return {"devices": device_names(_load_devices_json())}

@app.post("/devices/names")
def devices_names_set(req: DeviceNamesUpdate):
    with _devices_lock:
        cfg = _load_devices_json()
        by_mac = {device_mac(d.get("mac")): d for d in cfg["devices"]}
        # Check the entire batch before changing anything; statuses/discovery stay intact.
        for edit in req.changes:
            current = by_mac.get(edit.mac)
            if current is None:
                raise HTTPException(status_code=409, detail=f"Табличка {edit.mac} удалена. Обновите список.")
            if current.get("name", "") not in (edit.base_name, edit.name):
                raise HTTPException(status_code=409, detail=f"Название таблички {edit.mac} уже изменено другим оператором. Обновите список; ваши правки остаются в окне.")
        changed = False
        for edit in req.changes:
            if by_mac[edit.mac].get("name", "") != edit.name:
                by_mac[edit.mac]["name"] = edit.name
                changed = True
        if changed:
            _save_devices_json(cfg)
        return {"status": "ok", "devices": device_names(cfg)}

def merge_device_edits(base: List[dict], edited: List[dict], current: List[dict]) -> List[dict]:
    base_by_mac = {d["mac"]: d for d in base}
    current_by_mac = {d["mac"]: d for d in current}
    edited_by_mac = {d["mac"]: d for d in edited}

    def conflict(mac):
        raise HTTPException(status_code=409, detail=(
            f"Табличка {mac} изменена после открытия Config. "
            "Ваш текст сохранён в редакторе. Скопируйте правки и перезагрузите список перед повторным сохранением."
        ))

    for mac, old in base_by_mac.items():
        if mac not in edited_by_mac and mac in current_by_mac and current_by_mac[mac] != old:
            conflict(mac)

    merged = []
    for edit in edited:
        mac = edit["mac"]
        old = base_by_mac.get(mac)
        latest = current_by_mac.get(mac)
        if latest is None:
            if old is None:
                merged.append(edit)
            elif edit != old:
                conflict(mac)
            continue
        # A concurrently discovered MAC may also have been entered manually.
        if old is None:
            old = DeviceRecord(mac=mac).model_dump()
        result = dict(latest)
        for field in ("name", "profile", "status"):
            if edit[field] != old[field]:
                if latest[field] != old[field] and latest[field] != edit[field]:
                    conflict(mac)
                result[field] = edit[field]
        merged.append(result)

    merged.extend(d for d in current if d["mac"] not in base_by_mac and d["mac"] not in edited_by_mac)
    return merged

@app.get("/devices/file")
def devices_file_get():
    return _load_devices_json()

@app.post("/devices/file")
def devices_file_set(req: DevicesFile):
    with _devices_lock:
        cfg = _load_devices_json()
        try:
            current = [DeviceRecord(**d).model_dump() for d in cfg["devices"]]
            if len({d["mac"] for d in current}) != len(current):
                raise ValueError("MAC-адреса повторяются")
        except ValueError as exc:
            raise HTTPException(status_code=500, detail=f"Некорректный devices.json: {exc}") from exc
        merged = merge_device_edits(
            [d.model_dump() for d in req.base_devices], [d.model_dump() for d in req.devices], current,
        )
        if merged != cfg["devices"]:
            cfg["devices"] = merged
            _save_devices_json(cfg)
        return {"status": "ok", "count": len(merged), "devices": merged}

