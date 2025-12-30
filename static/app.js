const $ = (id) => document.getElementById(id);

const apiBase = $("apiBase");
const gwHost = $("gwHost");

const titleText = $("titleText");
const roleText = $("roleText");
const deviceSelect = $("deviceSelect");
const macValue = $("macValue");
const previewMac = $("previewMac");
const macManual = $("macManual");
const copyMac = $("copyMac");

const fontSize = $("fontSize");
const fontSizeValue = $("fontSizeValue");

const fioColor = $("fioColor");
const fioBold = $("fioBold");
const fioItalic = $("fioItalic");
const fioUnderline = $("fioUnderline");
const fioPosGrid = $("fioPosGrid");

const roleColor = $("roleColor");
const roleSize = $("roleSize");
const roleSizeValue = $("roleSizeValue");
const roleOffset = $("roleOffset");
const roleOffsetValue = $("roleOffsetValue");

const bgFile = $("bgFile");
const fileHint = $("fileHint");

const canvas = $("preview");
const ctx = canvas.getContext("2d");

const pushBtn = $("pushBtn");
const statusEl = $("status");

let bgImage = null;
let bgImageBase64 = null;
let selectedMac = "";
let selectedProfile = "nameplate7";
let loadDevicesToken = 0;

const fioStyle = {
  bold: false,
  italic: false,
  underline: false,
  position: "center",
  color: "#f3f5f7",
};

const roleStyle = {
  color: "#e5e7eb",
};


function setStatus(text) {
  statusEl.textContent = text;
}

function normMac(s) {
  return (s || "").replace(/[^0-9a-fA-F]/g, "").toLowerCase();
}

function currentApi() {
  const v = (apiBase.value || "").trim();
  if (!v) return window.location.origin;
  return v.replace(/\/+$/, "");
}

function dataURLToBase64(dataUrl) {
  if (!dataUrl) return null;
  const idx = dataUrl.indexOf(",");
  return idx >= 0 ? dataUrl.slice(idx + 1) : dataUrl;
}

function readFileAsDataURL(file) {
  return new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => resolve(fr.result);
    fr.onerror = () => reject(new Error("FileReader error"));
    fr.readAsDataURL(file);
  });
}

function wrapLines(text, maxWidth) {
  const words = (text || "").trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return [" "];

  const lines = [];
  let line = "";

  for (const w of words) {
    const test = (line ? line + " " : "") + w;
    const wpx = ctx.measureText(test).width;
    if (wpx <= maxWidth || !line) {
      line = test;
    } else {
      lines.push(line);
      line = w;
    }
  }
  if (line) lines.push(line);
  return lines;
}

function wrapFioSmart(fio, maxWidth, fontStr) {
  const parts = (fio || "").trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return [" "];

  ctx.font = fontStr;
  const oneLineW = ctx.measureText(parts.join(" ")).width;
  if (oneLineW <= maxWidth) return [parts.join(" ")];

  // правило: фамилия отдельно, остальное — на новую строку
  if (parts.length >= 3) {
    const line1 = parts[0];
    const rest = parts.slice(1).join(" ");
    // rest может быть длинным — доворрапим обычным способом
    const restLines = wrapLines(rest, maxWidth);
    return [line1, ...restLines];
  }

  // fallback
  return wrapLines(parts.join(" "), maxWidth);
}

function fontString(sizePx, {bold=false, italic=false} = {}) {
  const style = italic ? "italic " : "";
  const weight = bold ? "900 " : "800 ";
  // system fonts are fine for canvas preview; backend uses TTF
  return `${style}${weight}${sizePx}px system-ui, -apple-system, Segoe UI, Roboto, Arial`;
}

function calcAnchor(position) {
  // returns [ax, ay] in 0..1 space where 0=left/top, 0.5=center, 1=right/bottom
  switch (position) {
    case "top-left": return [0, 0];
    case "top": return [0.5, 0];
    case "top-right": return [1, 0];
    case "left": return [0, 0.5];
    case "center": return [0.5, 0.5];
    case "right": return [1, 0.5];
    case "bottom-left": return [0, 1];
    case "bottom": return [0.5, 1];
    case "bottom-right": return [1, 1];
    default: return [0.5, 0.5];
  }
}

function drawUnderline(x, y, w, color, thickness=3) {
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = thickness;
  ctx.beginPath();
  ctx.moveTo(x, y);
  ctx.lineTo(x + w, y);
  ctx.stroke();
  ctx.restore();
}

function drawPreview() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  // background
  if (bgImage) {
    const cw = canvas.width, ch = canvas.height;
    const iw = bgImage.width, ih = bgImage.height;
    const cr = cw / ch, ir = iw / ih;

    let sx=0, sy=0, sw=iw, sh=ih;
    if (ir > cr) {
      sw = ih * cr;
      sx = (iw - sw) / 2;
    } else {
      sh = iw / cr;
      sy = (ih - sh) / 2;
    }
    ctx.drawImage(bgImage, sx, sy, sw, sh, 0, 0, cw, ch);
  } else {
    const g = ctx.createLinearGradient(0, 0, 0, canvas.height);
    g.addColorStop(0, "#121b2b");
    g.addColorStop(1, "#0b1220");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, canvas.width, canvas.height);
  }

  const margin = 48;
  const maxWidth = canvas.width - margin * 2;

  const fio = (titleText.value || "").trim() || " ";
  const fioSize = parseInt(fontSize.value, 10) || 90;

  const role = (roleText?.value || "").trim();
  const roleSizePx = parseInt(roleSize?.value, 10) || 64;
  const roleOffsetPx = parseInt(roleOffset?.value, 10) || 18;

  // Compose styles from UI
  fioStyle.color = fioColor?.value || fioStyle.color;
  roleStyle.color = roleColor?.value || roleStyle.color;

  // Measure and wrap
  const fioFontStr = fontString(fioSize, fioStyle);
  const fioLines = wrapFioSmart(fio, maxWidth, fioFontStr);
  ctx.font = fioFontStr;

  ctx.font = fontString(roleSizePx, {bold:false, italic:false});
  const roleLines = role ? wrapLines(role, maxWidth) : [];

  // Line metrics (approx)
  const fioLineH = Math.round(fioSize * 1.15);
  const roleLineH = Math.round(roleSizePx * 1.15);

  const blockH = fioLines.length * fioLineH + (roleLines.length ? (roleOffsetPx + roleLines.length * roleLineH) : 0);

  // Determine block width as max line width
  let blockW = 0;
  ctx.font = fontString(fioSize, fioStyle);
  for (const ln of fioLines) blockW = Math.max(blockW, ctx.measureText(ln).width);
  ctx.font = fontString(roleSizePx, {bold:false, italic:false});
  for (const ln of roleLines) blockW = Math.max(blockW, ctx.measureText(ln).width);

  const [ax, ay] = calcAnchor(fioStyle.position || "center");
  const x0 = Math.round(margin + (maxWidth - blockW) * ax);
  const y0 = Math.round(margin + (canvas.height - margin * 2 - blockH) * ay);

  // alignment per position
  let align = "center";
  if ((fioStyle.position || "").includes("left")) align = "left";
  if ((fioStyle.position || "").includes("right")) align = "right";

  // draw fio
  ctx.save();
  ctx.textBaseline = "top";
  ctx.textAlign = align;

  ctx.shadowColor = "rgba(0,0,0,0.35)";
  ctx.shadowBlur = 6;
  ctx.shadowOffsetY = 2;

  ctx.fillStyle = fioStyle.color;
  ctx.font = fontString(fioSize, fioStyle);

  let xText = x0;
  if (align === "center") xText = x0 + blockW / 2;
  if (align === "right") xText = x0 + blockW;

  let y = y0;
  for (const ln of fioLines) {
    ctx.fillText(ln, xText, y);
    if (fioStyle.underline) {
      const w = ctx.measureText(ln).width;
      const ux = align === "left" ? xText : (align === "center" ? xText - w / 2 : xText - w);
      drawUnderline(ux, y + fioSize + 6, w, fioStyle.color, Math.max(2, Math.round(fioSize / 40)));
    }
    y += fioLineH;
  }

  // draw role
  if (roleLines.length) {
    y += roleOffsetPx;
    ctx.shadowColor = "rgba(0,0,0,0.25)";
    ctx.shadowBlur = 4;
    ctx.shadowOffsetY = 2;
    ctx.fillStyle = roleStyle.color;
    ctx.font = fontString(roleSizePx, {bold:false, italic:false});

    for (const ln of roleLines) {
      ctx.fillText(ln, xText, y);
      y += roleLineH;
    }
  }

  ctx.restore();
}

async function httpJson(url, opts = {}) {
  const res = await fetch(url, opts);
  const txt = await res.text();
  let data = null;
  try { data = txt ? JSON.parse(txt) : null; } catch { /* ignore */ }

  if (!res.ok) {
    const msg = data?.detail || data?.message || txt || `HTTP ${res.status}`;
    throw new Error(msg);
  }
  return data;
}

// ---------------- Debounce ----------------
let gwTimer = null;
function scheduleGatewaySet() {
  if (gwTimer) clearTimeout(gwTimer);
  gwTimer = setTimeout(async () => {
    const host = (gwHost.value || "").trim();
    if (!host) return;
    try {
      setStatus("Применяю шлюз...");
      await httpJson(`${currentApi()}/gateway/set`, {
        method: "POST",
        headers: {"Content-Type":"application/json"},
        body: JSON.stringify({ gateway_host: host, gateway_port: 5003 })
      });
      setStatus(`Шлюз установлен: ${host}:5003`);
      await loadDevices();
    } catch (e) {
      setStatus(`Ошибка gateway/set: ${e.message}`);
    }
  }, 350);
}

gwHost.addEventListener("input", scheduleGatewaySet);

// ---------------- DeviceList ----------------
async function loadGatewayCurrent() {
  try {
    const gw = await httpJson(`${currentApi()}/gateway`);
    gwHost.value = gw.gateway_host || gwHost.value;
  } catch {
    // ignore
  }
}

async function loadDevices() {
  const myToken = ++loadDevicesToken;
  setStatus("Загружаю список табличек...");

  // ✅ всегда чистим список в начале
  deviceSelect.innerHTML = `<option value="">ID таблички (загрузка)...</option>`;

  try {
    const data = await httpJson(`${currentApi()}/devices/list`);
    if (myToken !== loadDevicesToken) return; // ✅ если пришёл старый ответ — игнор

    if (data.gateway_reachable === false) {
      deviceSelect.innerHTML = `<option value="">ID таблички (нет связи со шлюзом)...</option>`;
      setStatus("Нет связи со шлюзом — список табличек скрыт. Проверьте сеть/шлюз.");
      return;
    }

    const list = data.devices || [];

    // ✅ чистим и добавляем нормальную заглушку
    deviceSelect.innerHTML = `<option value="">ID таблички (выберите)...</option>`;

    for (const dev of list) {
      if ((dev.status_local || "base") !== "base") continue;

      const mac = dev.mac;
      const name = dev.name || mac;
      const ip = dev.ip ? ` — ${dev.ip}` : "";
      const profile = dev.profile || "nameplate7";

      const opt = document.createElement("option");
      opt.value = mac;
      opt.dataset.profile = profile;
      opt.textContent = `${name}${ip}`;
      deviceSelect.appendChild(opt);
    }

    setStatus(`Список загружен. Показано: ${deviceSelect.options.length - 1} (из ${list.length}).`);
  } catch (e) {
    setStatus(`Ошибка devices/list: ${e.message}`);
    deviceSelect.innerHTML = `<option value="">ID таблички (ошибка загрузки)...</option>`;
  }
}



function setMac(mac) {
  selectedMac = normMac(mac);
  macValue.textContent = selectedMac ? selectedMac.toUpperCase() : "?";
  previewMac.textContent = selectedMac ? selectedMac : "—";
}

deviceSelect.addEventListener("change", () => {
  setMac(deviceSelect.value);
  const opt = deviceSelect.selectedOptions?.[0];
  selectedProfile = opt?.dataset?.profile || "nameplate7";
});

copyMac.addEventListener("click", () => {
  const m = normMac(macManual.value);
  if (!m) {
    setStatus("В поле ручного MAC пусто.");
    return;
  }

  const answer = prompt(
    "Укажите размер таблички:\n\n" +
    "7  — для 7 дюймов\n" +
    "10 — для 10 дюймов",
    "10"
  );

  if (answer === null) {
    setStatus("Выбор формата отменён.");
    return;
  }

  let profile = null;
  if (answer.trim() === "7") profile = "nameplate7";
  if (answer.trim() === "10") profile = "nameplate10";

  if (!profile) {
    setStatus("Неверный формат. Введите 7 или 10.");
    return;
  }

  setMac(m);
  selectedProfile = profile;

  setStatus(`MAC установлен вручную (${profile}).`);
});


fontSize.addEventListener("input", () => {
  fontSizeValue.textContent = `${fontSize.value}px`;
  drawPreview();
});

// v3 controls
function toggleBtn(btn, key){
  btn.addEventListener("click", (e)=>{
    e.preventDefault();
    fioStyle[key] = !fioStyle[key];
    btn.classList.toggle("active", fioStyle[key]);
    drawPreview();
  });
}
if (fioBold) toggleBtn(fioBold, "bold");
if (fioItalic) toggleBtn(fioItalic, "italic");
if (fioUnderline) toggleBtn(fioUnderline, "underline");
if (fioColor) fioColor.addEventListener("input", drawPreview);

function setPos(pos){
  fioStyle.position = pos;
  if (fioPosGrid){
    fioPosGrid.querySelectorAll(".posbtn").forEach(b=>b.classList.toggle("active", b.dataset.pos===pos));
  }
  drawPreview();
}
if (fioPosGrid){
  fioPosGrid.addEventListener("click", (e)=>{
    const btn = e.target.closest(".posbtn");
    if (!btn) return;
    e.preventDefault();
    setPos(btn.dataset.pos || "center");
  });
}

if (roleText) roleText.addEventListener("input", drawPreview);
if (roleColor) roleColor.addEventListener("input", drawPreview);
if (roleSize){
  roleSize.addEventListener("input", ()=>{
    if (roleSizeValue) roleSizeValue.textContent = `${roleSize.value}px`;
    drawPreview();
  });
}
if (roleOffset){
  roleOffset.addEventListener("input", ()=>{
    if (roleOffsetValue) roleOffsetValue.textContent = `${roleOffset.value}px`;
    drawPreview();
  });
}


titleText.addEventListener("input", drawPreview);

bgFile.addEventListener("change", async () => {
  const f = bgFile.files && bgFile.files[0];
  if (!f) {
    bgImage = null;
    bgImageBase64 = null;
    fileHint.textContent = "Файл: не выбран";
    drawPreview();
    return;
  }

  fileHint.textContent = `Файл: ${f.name}`;

  // ---------------- Preview ----------------
  const url = URL.createObjectURL(f);
  const img = new Image();
  img.onload = () => {
    bgImage = img;
    drawPreview();
    URL.revokeObjectURL(url);
  };
  img.src = url;

  try {
    const dataUrl = await readFileAsDataURL(f);
    bgImageBase64 = dataURLToBase64(dataUrl);
  } catch (e) {
    bgImageBase64 = null;
    setStatus(`Не смог прочитать фон: ${e.message}`);
  }
});

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function push() { 
  const mac = selectedMac || normMac(macManual.value);
  if (!mac) {
    setStatus("Нужен MAC: выберите табличку или введите вручную.");
    return;
  }

  const api = currentApi();
  setStatus(`Отправляю на ${api}/push ...`);

  const req = {
    mac,
    render: {
      profile: selectedProfile || "nameplate7",
      background: "#0B1220",
      background_image_base64: bgImageBase64 ? `data:image/png;base64,${bgImageBase64}` : null,
      text: {
        fio: {
          value: (titleText.value || "").trim() || " ",
          color: fioColor ? fioColor.value : "#f3f5f7",
          size: parseInt(fontSize.value, 10),
          bold: !!fioStyle.bold,
          italic: !!fioStyle.italic,
          underline: !!fioStyle.underline,
          position: fioStyle.position || "center",
        },
        title: {
          value: (roleText?.value || "").trim() || "",
          color: roleColor ? roleColor.value : "#e5e7eb",
          size: roleSize ? parseInt(roleSize.value, 10) : 64,
          offset_y: roleOffset ? parseInt(roleOffset.value, 10) : 18
        }
      }
    }
  };

  try {
    const data = await httpJson(`${api}/push`, {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify(req),
    });

    const sentSeq = data.sent_seq;

    try {
      await httpJson(`${api}/devices/mark_filled`, {
        method: "POST",
        headers: {"Content-Type":"application/json"},
        body: JSON.stringify({ mac })
      });

      await loadDevices();
      deviceSelect.value = "";
      setMac("");
    } catch (eMark) {
      setStatus(`PUSH ok (sent_seq=${sentSeq}), но не смог пометить filled: ${eMark.message}`);
      return;
    }

    setStatus(`PUSH ok. Табличка скрыта. sent_seq=${sentSeq}. Проверяю статус устройства...`);

    let last = null;
    for (let i = 1; i <= 5; i++) {
      await sleep(700);
      try {
        const st = await httpJson(`${api}/push/status`, {
          method: "POST",
          headers: {"Content-Type":"application/json"},
          body: JSON.stringify({ mac, response_seq: sentSeq }),
        });

        last = st;
        const s = Number(st.status);

        if (s === 1) {
          setStatus(`Готово: устройство подтвердило обновление (status=1).`);
          return;
        } else {
          setStatus(`PUSH ok, место скрыто. Статус пока ${st.status} (попытка ${i}/5)`);
        }
      } catch (e2) {
        setStatus(`PUSH ok, место скрыто. Ошибка status-check (попытка ${i}/5): ${e2.message}`);
      }
    }

    if (last) {
      setStatus(`PUSH ok, место скрыто. Подтверждения не дождались: status=${last.status}.`);
    } else {
      setStatus(`PUSH ok, место скрыто. Подтверждения статуса не получили.`);
    }

  } catch (e) {
    setStatus(`Ошибка PUSH: ${e.message}`);
  }
}


pushBtn.addEventListener("click", push);

// ---------------- Init ----------------
if (!apiBase.value) apiBase.value = window.location.origin;

// init v3 values
if (roleSizeValue && roleSize) roleSizeValue.textContent = `${roleSize.value}px`;
if (roleOffsetValue && roleOffset) roleOffsetValue.textContent = `${roleOffset.value}px`;

drawPreview();
loadGatewayCurrent().finally(loadDevices);

const refreshDevices = $("refreshDevices");
refreshDevices.addEventListener("click", async () => {
  try {
    await httpJson(`${currentApi()}/devices/refresh`, { method: "POST" });
    setStatus("Список сброшен: все таблички снова в состоянии base.");
    await loadDevices();
  } catch (e) {
    setStatus(`Ошибка refresh: ${e.message}`);
  }
});

// -------- Config editor --------
const configBtn = $("configBtn");
const configModal = $("configModal");
const configClose = $("configClose");
const configTextarea = $("configTextarea");
const configReload = $("configReload");
const configSave = $("configSave");
const configStatus = $("configStatus");

function showConfigModal(show) {
  if (show) {
    configModal.classList.add("show");
    configModal.setAttribute("aria-hidden", "false");
  } else {
    configModal.classList.remove("show");
    configModal.setAttribute("aria-hidden", "true");
  }
}

function setConfigStatus(msg) {
  configStatus.textContent = msg;
}

async function loadConfigToEditor() {
  try {
    setConfigStatus("Загружаю devices.json...");
    const cfg = await httpJson(`${currentApi()}/devices/file`);
    configTextarea.value = JSON.stringify(cfg, null, 2);
    setConfigStatus("Загружено.");
  } catch (e) {
    setConfigStatus(`Ошибка загрузки: ${e.message}`);
  }
}

async function saveConfigFromEditor() {
  let obj;
  try {
    obj = JSON.parse(configTextarea.value);
  } catch (e) {
    setConfigStatus(`JSON некорректен: ${e.message}`);
    return;
  }

  if (!obj.gateway_host || typeof obj.gateway_host !== "string") {
    setConfigStatus("Нужно поле gateway_host (строка).");
    return;
  }
  if (!obj.gateway_port || typeof obj.gateway_port !== "number") {
    setConfigStatus("Нужно поле gateway_port (число).");
    return;
  }

  try {
    setConfigStatus("Сохраняю...");
    const obj = JSON.parse(configTextarea.value);
    const res = await httpJson(`${currentApi()}/devices/file`, {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify(obj)
    });

    if (res?.config?.gateway_host) gwHost.value = res.config.gateway_host;

    setConfigStatus("Сохранено и применено.");
    await loadDevices();
  } catch (e) {
    setConfigStatus(`Ошибка сохранения: ${e.message}`);
  }
}

configBtn.addEventListener("click", async (e) => {
  e.preventDefault();
  showConfigModal(true);
  await loadConfigToEditor();
});

configClose.addEventListener("click", () => showConfigModal(false));
configModal.addEventListener("click", (e) => {
  if (e.target === configModal) showConfigModal(false);
});

configReload.addEventListener("click", loadConfigToEditor);
configSave.addEventListener("click", saveConfigFromEditor);

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && configModal.classList.contains("show")) {
    showConfigModal(false);
  }
});
