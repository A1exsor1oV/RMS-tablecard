const $ = (id) => document.getElementById(id);

const titleText = $("titleText");
const roleText = $("roleText");
const deviceSelect = $("deviceSelect");
const deviceListHint = $("deviceListHint");
const macValue = $("macValue");
const previewMac = $("previewMac");

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
const fioFont = $("fioFont");
const roleFont = $("roleFont");

const bgFile = $("bgFile");
const fileHint = $("fileHint");

const previewImage = $("preview");
const previewBox = $("previewBox");
const previewStatus = $("previewStatus");
const previewRetry = $("previewRetry");

const pushBtn = $("pushBtn");

let bgImageBase64 = null;
let backgroundLoading = false;
let backgroundError = "";
let backgroundRevision = 0;
let previewTimer = null;
let previewController = null;
let previewRevision = 0;
let previewSnapshot = null;
let selectedMac = "";
let selectedProfile = "nameplate7";
let loadDevicesToken = 0;
let deviceListLoading = false;
let pushInProgress = false;

const fioStyle = {
  bold: false,
  italic: false,
  underline: false,
  position: "center",
  color: "#f3f5f7",
};

const notice = $("notice");
let noticeTimer = null;
function showNotice(message, error = false) {
  clearTimeout(noticeTimer);
  $("noticeText").textContent = message;
  notice.dataset.error = String(error);
  notice.hidden = false;
  if (!error) noticeTimer = setTimeout(() => { notice.hidden = true; }, 6000);
}
$("noticeClose").addEventListener("click", () => {
  clearTimeout(noticeTimer);
  notice.hidden = true;
});

function normMac(s) {
  return (s || "").replace(/[^0-9a-fA-F]/g, "").toLowerCase();
}

function currentApi() {
  return window.location.origin;
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

// Preview and PUSH use the same server renderer and the same immutable settings.
function buildRenderRequest() {
  return {
    profile: selectedProfile || "nameplate7",
    background: "#0B1220",
    background_image_base64: bgImageBase64,
    text: {
      fio: {
        value: (titleText.value || "").trim() || " ",
        font_family: fioFont.value || "default",
        color: fioColor.value,
        size: parseInt(fontSize.value, 10),
        bold: fioStyle.bold,
        italic: fioStyle.italic,
        underline: fioStyle.underline,
        position: fioStyle.position,
      },
      title: {
        value: (roleText.value || "").trim(),
        font_family: roleFont.value || "default",
        color: roleColor.value,
        size: parseInt(roleSize.value, 10),
        offset_y: parseInt(roleOffset.value, 10),
      },
    },
  };
}

function updatePushAvailability() {
  pushBtn.disabled = pushInProgress || !selectedMac || backgroundLoading || !previewSnapshot
    || previewSnapshot.key !== JSON.stringify(buildRenderRequest());
}

function drawPreview() {
  clearTimeout(previewTimer);
  previewController?.abort();
  const revision = ++previewRevision;
  previewSnapshot = null;
  previewImage.hidden = true;
  previewRetry.hidden = true;
  previewBox.setAttribute("aria-busy", backgroundError ? "false" : "true");
  previewStatus.textContent = backgroundError
    || (backgroundLoading ? "Читаю изображение фона…" : "Обновляю предпросмотр…");
  updatePushAvailability();
  if (backgroundLoading || backgroundError) return;
  // Coalesce typing/slider events; stale responses never replace newer settings.
  previewTimer = setTimeout(() => renderPreview(revision), 180);
}

async function renderPreview(revision) {
  const request = buildRenderRequest();
  const key = JSON.stringify(request);
  const controller = new AbortController();
  previewController = controller;
  try {
    const data = await httpJson(`${currentApi()}/render`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: key,
      signal: controller.signal,
    });
    if (revision !== previewRevision) return;
    if (!data?.jpg_base64 || !/^[0-9a-f]{64}$/.test(data.jpg_sha256)
        || data.profile !== request.profile || !(data.width > 0 && data.height > 0)) {
      throw new Error("Сервер вернул неполный предпросмотр. Перезапустите сервис после обновления.");
    }
    const image = new Image();
    image.src = `data:image/jpeg;base64,${data.jpg_base64}`;
    await image.decode();
    if (revision !== previewRevision || key !== JSON.stringify(buildRenderRequest())) return;
    previewImage.width = data.width;
    previewImage.height = data.height;
    previewImage.src = image.src;
    previewBox.style.aspectRatio = `${data.width} / ${data.height}`;
    previewImage.hidden = false;
    previewSnapshot = {request, key, sha256: data.jpg_sha256};
    const inches = data.profile === "nameplate10" ? "10" : "7";
    previewStatus.textContent = `${inches}″ · ${data.width} × ${data.height}`;
    previewBox.setAttribute("aria-busy", "false");
    updatePushAvailability();
  } catch (error) {
    if (revision !== previewRevision || error.name === "AbortError") return;
    previewStatus.textContent = `Не удалось подготовить предпросмотр: ${error.message}`;
    previewBox.setAttribute("aria-busy", "false");
    previewRetry.hidden = false;
    updatePushAvailability();
  }
}

previewRetry.addEventListener("click", drawPreview);

function setProfile(profile) {
  const next = profile || "nameplate7";
  if (next !== selectedProfile) {
    selectedProfile = next;
    drawPreview();
  }
}

async function httpJson(url, opts = {}) {
  const res = await fetch(url, { cache: "no-store", ...opts });
  const txt = await res.text();
  let data = null;
  try { data = txt ? JSON.parse(txt) : null; } catch { /* ignore */ }

  if (!res.ok) {
    const detail = data?.detail;
    const msg = Array.isArray(detail)
      ? detail.map(item => `${(item.loc || []).join(".")}: ${item.msg}`).join("; ")
      : detail || data?.message || txt || `HTTP ${res.status}`;
    throw new Error(msg);
  }
  return data;
}

// ---------------- DeviceList ----------------
async function loadDevices({ quiet = false, force = false } = {}) {
  if (quiet && !force && (deviceListLoading || pushInProgress || document.hidden)) return;
  const myToken = ++loadDevicesToken;
  deviceListLoading = true;

  function clearSelection() {
    const previousSelection = deviceSelect.value;
    if (previousSelection && selectedMac === previousSelection) setMac("");
  }

  try {
    const data = await httpJson(`${currentApi()}/devices/list`);
    if (myToken !== loadDevicesToken) return; // ✅ если пришёл старый ответ — игнор

    if (data.gateway_reachable === false) {
      clearSelection();
      deviceSelect.innerHTML = `<option value="">ID таблички (нет связи со шлюзом)...</option>`;
      deviceListHint.textContent = `Нет связи со шлюзом ${data.gateway || ""}. Проверьте питание шлюза и сетевой кабель. Сохранённые названия доступны в меню «Таблички».`;
      deviceListHint.dataset.error = "true";
      return false;
    }

    const list = data.devices || [];
    const counts = data.counts || {};
    deviceListHint.dataset.error = "false";
    if (list.length) {
      deviceListHint.textContent = `Доступно табличек: ${list.length}. Уже заполнены: ${counts.filled || 0}.`;
    } else if (!counts.registered) {
      deviceListHint.textContent = "Таблички ещё не обнаружены. Включите их и нажмите «Найти новые таблички».";
    } else if (counts.filled === counts.registered) {
      deviceListHint.textContent = "Все таблички уже заполнены и скрыты. Кнопка ⟳ вернёт их в список для повторной отправки.";
    } else {
      deviceListHint.textContent = `Нет доступных табличек: заполнены — ${counts.filled || 0}, не в сети — ${counts.offline || 0}, не отвечают — ${counts.unreachable || 0}. Проверьте питание табличек и подключение к шлюзу.`;
    }

    const previousSelection = deviceSelect.value;
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

    const selectedOption = Array.from(deviceSelect.options).find(option => option.value === previousSelection);
    if (previousSelection && selectedOption) {
      deviceSelect.value = previousSelection;
      setProfile(selectedOption.dataset.profile);
    } else if (previousSelection && selectedMac === previousSelection) {
      setMac("");
    }
    return true;
  } catch (e) {
    if (myToken !== loadDevicesToken) return;
    clearSelection();
    deviceSelect.innerHTML = `<option value="">ID таблички (ошибка загрузки)...</option>`;
    deviceListHint.textContent = `Не удалось загрузить таблички: ${e.message}`;
    deviceListHint.dataset.error = "true";
    return false;
  } finally {
    if (myToken === loadDevicesToken) deviceListLoading = false;
  }
}



function setMac(mac) {
  selectedMac = normMac(mac);
  macValue.textContent = selectedMac ? selectedMac.toUpperCase() : "?";
  previewMac.textContent = selectedMac ? selectedMac : "—";
  updatePushAvailability();
}

deviceSelect.addEventListener("change", () => {
  setMac(deviceSelect.value);
  const opt = deviceSelect.selectedOptions?.[0];
  setProfile(opt?.dataset?.profile);
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

fioFont.addEventListener("change", drawPreview);
roleFont.addEventListener("change", drawPreview);

async function loadFonts() {
  const hint = $("fontStatus");
  try {
    const result = await httpJson(`${currentApi()}/fonts`);
    if (!result?.fonts?.length) throw new Error("На сервере нет доступных шрифтов.");
    const before = JSON.stringify(buildRenderRequest());
    for (const select of [fioFont, roleFont]) {
      const previous = select.value;
      select.replaceChildren(...result.fonts.map(font => new Option(font.name, font.id)));
      select.value = result.fonts.some(font => font.id === previous) ? previous : result.fonts[0].id;
      select.disabled = false;
    }
    hint.hidden = true;
    if (before !== JSON.stringify(buildRenderRequest())) drawPreview();
  } catch (error) {
    hint.textContent = `Не удалось загрузить список шрифтов: ${error.message}`;
    hint.hidden = false;
  }
}


titleText.addEventListener("input", drawPreview);

bgFile.addEventListener("change", async () => {
  const revision = ++backgroundRevision;
  const file = bgFile.files?.[0];
  bgImageBase64 = null;
  backgroundError = "";
  backgroundLoading = !!file;
  fileHint.textContent = file ? `Файл: ${file.name}` : "Файл: не выбран";
  drawPreview();
  if (!file) return;

  try {
    const dataUrl = await readFileAsDataURL(file);
    if (revision !== backgroundRevision) return;
    bgImageBase64 = dataURLToBase64(dataUrl);
  } catch (error) {
    if (revision !== backgroundRevision) return;
    backgroundError = `Не удалось прочитать фон: ${error.message}. Выберите файл заново.`;
  } finally {
    if (revision === backgroundRevision) {
      backgroundLoading = false;
      drawPreview();
    }
  }
});

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function push() { 
  if (pushInProgress) return;
  const mac = selectedMac;
  if (!mac) {
    showNotice("Выберите табличку из списка.", true);
    return;
  }

  const snapshot = previewSnapshot;
  if (backgroundLoading || !snapshot || snapshot.key !== JSON.stringify(buildRenderRequest())) {
    showNotice("Дождитесь готового предпросмотра перед отправкой.", true);
    return;
  }

  const api = currentApi();
  const req = {
    mac,
    render: snapshot.request,
    expected_jpg_sha256: snapshot.sha256,
  };

  try {
    pushInProgress = true;
    pushBtn.disabled = true;
    pushBtn.textContent = "…";
    pushBtn.title = "Отправляю изображение…";
    pushBtn.setAttribute("aria-busy", "true");
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

      await loadDevices({ quiet: true, force: true });
      deviceSelect.value = "";
      setMac("");
    } catch (eMark) {
      showNotice("Изображение отправлено, но табличку не удалось скрыть из списка. Не отправляйте его повторно.", true);
      return;
    }

    for (let i = 1; i <= 5; i++) {
      await sleep(700);
      try {
        const st = await httpJson(`${api}/push/status`, {
          method: "POST",
          headers: {"Content-Type":"application/json"},
          body: JSON.stringify({ mac, response_seq: sentSeq }),
        });

        const s = Number(st.status);

        if (s === 1) {
          showNotice("Изображение на табличке обновлено.");
          return;
        }
      } catch {
        // Retry confirmation; show only the final outcome to the operator.
      }
    }

    showNotice("Изображение отправлено. Подтверждение от таблички пока не получено — проверьте её экран.", true);

  } catch (e) {
    showNotice("Не удалось отправить изображение. Проверьте связь со шлюзом и повторите попытку.", true);
  } finally {
    pushInProgress = false;
    pushBtn.textContent = "⤴";
    pushBtn.title = "Отправить на табличку";
    pushBtn.setAttribute("aria-busy", "false");
    updatePushAvailability();
  }
}


pushBtn.addEventListener("click", push);

const refreshDevices = $("refreshDevices");
refreshDevices.addEventListener("click", async () => {
  refreshDevices.disabled = true;
  try {
    await httpJson(`${currentApi()}/devices/refresh`, { method: "POST" });
    await loadDevices({ quiet: true, force: true });
  } catch (e) {
    showNotice("Не удалось вернуть таблички в список. Повторите попытку.", true);
  } finally {
    refreshDevices.disabled = false;
  }
});

const discoverDevices = $("discoverDevices");
discoverDevices.addEventListener("click", async () => {
  discoverDevices.disabled = true;
  discoverDevices.textContent = "Ищу таблички…";
  try {
    await httpJson(`${currentApi()}/devices/discover`, { method: "POST" });
    await loadDevices({ quiet: true, force: true });
  } catch (e) {
    showNotice("Не удалось найти таблички. Проверьте связь со шлюзом и повторите поиск.", true);
  } finally {
    discoverDevices.disabled = false;
    discoverDevices.textContent = "Найти новые таблички";
  }
});

// -------- Operator device names --------
const namesBtn = $("namesBtn");
const namesModal = $("namesModal");
const namesList = $("namesList");
const namesSave = $("namesSave");
const namesReload = $("namesReload");
const namesClose = $("namesClose");
const namesStatus = $("namesStatus");
let nameRows = new Map();
let namesBusy = false;

function nameChanges() {
  return Array.from(nameRows, ([mac, row]) => ({mac, name: row.input.value.trim(), base_name: row.baseName}))
    .filter(edit => edit.name !== edit.base_name);
}

function updateNameControls() {
  namesSave.disabled = namesBusy || nameChanges().length === 0;
  namesReload.disabled = namesBusy;
  for (const row of nameRows.values()) row.input.disabled = namesBusy;
}

function renderNames(devices, drafts = new Map()) {
  nameRows = new Map();
  namesList.replaceChildren();
  const sorted = [...devices].sort((a, b) => Number(!!a.name.trim()) - Number(!!b.name.trim()));
  for (const device of sorted) {
    const row = document.createElement("label");
    row.className = "name-row";
    const identity = document.createElement("span");
    identity.className = "name-identity";
    const mac = document.createElement("span");
    mac.className = "name-mac";
    mac.textContent = device.mac.toUpperCase().match(/.{1,2}/g)?.join(":") || device.mac;
    identity.appendChild(mac);
    if (!device.name.trim()) {
      const badge = document.createElement("span");
      badge.className = "name-new";
      badge.textContent = "Без названия";
      identity.appendChild(badge);
    }
    const input = document.createElement("input");
    input.className = "name-input";
    input.maxLength = 120;
    input.placeholder = "Например, Место 1";
    input.value = drafts.has(device.mac) ? drafts.get(device.mac) : device.name;
    input.dataset.mac = device.mac;
    input.setAttribute("aria-label", `Название таблички ${mac.textContent}`);
    input.addEventListener("input", updateNameControls);
    nameRows.set(device.mac, {input, baseName: device.name});
    row.append(identity, input);
    namesList.appendChild(row);
  }
  if (!sorted.length) namesList.textContent = "Таблички ещё не обнаружены. Подключите шлюз и включите таблички.";
  updateNameControls();
}

async function loadNames() {
  if (namesBusy) return;
  const drafts = new Map(nameChanges().map(edit => [edit.mac, edit.name]));
  try {
    namesBusy = true;
    updateNameControls();
    namesStatus.textContent = "Загружаю таблички…";
    const result = await httpJson(`${currentApi()}/devices/names`);
    renderNames(result.devices, drafts);
    namesStatus.textContent = drafts.size
      ? "Список обновлён. Несохранённые названия сохранены в полях — проверьте и нажмите «Сохранить названия»."
      : `Всего табличек: ${result.devices.length}. Без названия: ${result.devices.filter(device => !device.name.trim()).length}.`;
  } catch (error) {
    namesStatus.textContent = `Не удалось загрузить таблички: ${error.message}`;
  } finally {
    namesBusy = false;
    updateNameControls();
  }
}

async function saveNames() {
  if (namesBusy) return;
  const changes = nameChanges();
  if (!changes.length) return;
  try {
    namesBusy = true;
    updateNameControls();
    namesStatus.textContent = "Сохраняю названия…";
    const result = await httpJson(`${currentApi()}/devices/names`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({changes}),
    });
    renderNames(result.devices);
    namesStatus.textContent = "Названия сохранены.";
    void loadDevices({quiet: true, force: true});
  } catch (error) {
    namesStatus.textContent = `Не удалось сохранить: ${error.message}`;
  } finally {
    namesBusy = false;
    updateNameControls();
  }
}

function showNames(show) {
  namesModal.classList.toggle("show", show);
  namesModal.setAttribute("aria-hidden", String(!show));
  document.body.style.overflow = show ? "hidden" : "";
  (show ? namesClose : namesBtn).focus();
}
namesBtn.addEventListener("click", () => { showNames(true); void loadNames(); });
namesClose.addEventListener("click", () => showNames(false));
namesModal.addEventListener("click", event => { if (event.target === namesModal) showNames(false); });
namesReload.addEventListener("click", loadNames);
namesSave.addEventListener("click", saveNames);
document.addEventListener("keydown", event => {
  if (!namesModal.classList.contains("show")) return;
  if (event.key === "Escape") showNames(false);
  if (event.key === "Tab") {
    const controls = [...namesModal.querySelectorAll("button:not(:disabled), input:not(:disabled)")];
    const first = controls[0], last = controls.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
});

// ---------------- Init ----------------
// Register controls before rendering the preview or loading remote data.
if (roleSizeValue && roleSize) roleSizeValue.textContent = `${roleSize.value}px`;
if (roleOffsetValue && roleOffset) roleOffsetValue.textContent = `${roleOffset.value}px`;

drawPreview();
loadFonts();
loadDevices();
setInterval(() => loadDevices({ quiet: true }), 15000);
