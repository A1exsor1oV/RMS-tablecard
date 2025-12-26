const $ = (id) => document.getElementById(id);

const apiBase = $("apiBase");
const gwHost = $("gwHost");

const titleText = $("titleText");
const deviceSelect = $("deviceSelect");
const macValue = $("macValue");
const previewMac = $("previewMac");
const macManual = $("macManual");
const copyMac = $("copyMac");

const fontSize = $("fontSize");
const fontSizeValue = $("fontSizeValue");

const bgFile = $("bgFile");
const fileHint = $("fileHint");

const canvas = $("preview");
const ctx = canvas.getContext("2d");

const pushBtn = $("pushBtn");
const statusEl = $("status");

let bgImage = null;
let bgImageBase64 = null; // <-- то, что реально уйдёт на бэк
let selectedMac = "";
let selectedProfile = "nameplate10";

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

function drawPreview() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  if (bgImage) {
    // cover
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

  const text = (titleText.value || "").trim();
  const size = parseInt(fontSize.value, 10);

  ctx.font = `800 ${size}px system-ui, -apple-system, Segoe UI, Roboto, Arial`;
  ctx.fillStyle = "#f3f5f7";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";

  ctx.shadowColor = "rgba(0,0,0,0.35)";
  ctx.shadowBlur = 6;
  ctx.shadowOffsetY = 2;

  ctx.fillText(text, canvas.width / 2, canvas.height / 2);

  ctx.shadowColor = "transparent";
  ctx.shadowBlur = 0;
  ctx.shadowOffsetY = 0;
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

// ---- gateway/set (debounce) ----
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
      await loadDevices(); // перезагрузим статусы/список
    } catch (e) {
      setStatus(`Ошибка gateway/set: ${e.message}`);
    }
  }, 350);
}

gwHost.addEventListener("input", scheduleGatewaySet);

// ---- devices/list from devices.json ----
async function loadGatewayCurrent() {
  try {
    const gw = await httpJson(`${currentApi()}/gateway`);
    gwHost.value = gw.gateway_host || gwHost.value;
  } catch {
    // ignore
  }
}

async function loadDevices() {
  setStatus("Загружаю список табличек (devices.json) ...");
  try {
    const data = await httpJson(`${currentApi()}/devices/list`);
    const list = data.devices || [];

    deviceSelect.innerHTML = `<option value="">ID таблички (выберите)...</option>`;

    for (const dev of list) {
      if ((dev.status_local || "base") !== "base") continue;

      const mac = dev.mac;
      const name = dev.name || mac;
      const ip = dev.ip ? ` — ${dev.ip}` : "";
      const profile = dev.profile || "nameplate10";

      const opt = document.createElement("option");
      opt.value = mac;
      opt.dataset.profile = profile;
      opt.textContent = `${name}${ip}`;
      deviceSelect.appendChild(opt);
    }

    setStatus(`Список загружен. Показано: ${deviceSelect.options.length - 1} (из ${list.length}).`);
  } catch (e) {
    setStatus(`Ошибка devices/list: ${e.message}`);
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
  selectedProfile = opt?.dataset?.profile || "nameplate10";
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

  // preview image
  const url = URL.createObjectURL(f);
  const img = new Image();
  img.onload = () => {
    bgImage = img;
    drawPreview();
    URL.revokeObjectURL(url);
  };
  img.src = url;

  // actual base64 for backend
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
      profile: selectedProfile || "nameplate10",
      background: "#0B1220",
      background_image_base64: bgImageBase64 ? `data:image/png;base64,${bgImageBase64}` : null,
      text: {
        text: (titleText.value || "").trim() || " ",
        color: "#F3F5F7",
        size: parseInt(fontSize.value, 10),
        align: "center",
        valign: "center",
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

    // ✅ Скрываем место сразу после успешного PUSH (для UX)
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
      // не критично для отправки, но важно увидеть
      setStatus(`PUSH ok (sent_seq=${sentSeq}), но не смог пометить filled: ${eMark.message}`);
      return;
    }

    setStatus(`PUSH ok. Табличка скрыта. sent_seq=${sentSeq}. Проверяю статус устройства...`);

    // 🔁 Poll status до 5 попыток
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

    // если так и не стало 1 — просто сообщаем
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

// init
if (!apiBase.value) apiBase.value = window.location.origin;
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

