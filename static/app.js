let state = null;
let activeTab = "bank";
let activeView = "library";
let activeReviewItem = null;
let selectedClientId = localStorage.getItem("maliyardimci:selectedClientId") || "";
const MAX_FUNCTION_UPLOAD_BYTES = 3 * 1024 * 1024;

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

const FIELD_LABELS = {
  id: "Kayıt no",
  item_type: "Tür",
  client_id: "Mükellef no",
  period: "Dönem",
  title: "Başlık",
  confidence: "Güven",
  detail: "Durum",
  bank_name: "Banka adı",
  account_no_or_iban: "Hesap / IBAN",
  date: "Tarih",
  description: "Açıklama",
  debit: "Borç",
  credit: "Alacak",
  balance: "Bakiye",
  currency: "Para birimi",
  counterparty_guess: "Karşı taraf",
  suggested_account_code: "Önerilen hesap kodu",
  duplicate_flag: "Mükerrer olabilir",
  report_date: "Rapor tarihi",
  device_brand: "Cihaz markası",
  device_serial: "Cihaz seri no",
  z_no: "Z no",
  gross_total: "Toplam tutar",
  vat_lines: "KDV satırları",
  payment_breakdown: "Ödeme dağılımı",
  source_file: "Belge",
  receipt_date: "Fiş tarihi",
  merchant_name: "Satıcı adı",
  vkn_tckn: "VKN/TCKN",
  document_no: "Belge no",
  vat_total: "KDV tutarı",
  payment_method: "Ödeme şekli",
  bookkeeping_status: "İşleme durumu",
  needs_review: "Kontrol durumu",
  module: "Bölüm",
  original_name: "Yüklenen belge",
  status: "İşlem durumu",
  warnings: "Uyarılar",
  created_at: "Oluşturulma zamanı",
};

const TYPE_LABELS = {
  bank: "Banka",
  z: "Z raporu",
  receipt: "Fiş",
};

const VALUE_LABELS = {
  uygun: "Uygun",
  eksik: "Eksik",
  okunamadi: "Okunamadı",
  manuel_kontrol: "Elle kontrol",
  islenmez: "İşlenmez",
  processing: "İşleniyor",
  done: "Tamamlandı",
  failed: "Başarısız",
};

const REVIEW_FIELDS = {
  bank: ["date", "description", "debit", "credit", "balance", "currency", "counterparty_guess", "suggested_account_code", "duplicate_flag"],
  z: ["report_date", "device_brand", "device_serial", "z_no", "gross_total", "vat_lines", "payment_breakdown"],
  receipt: ["receipt_date", "merchant_name", "vkn_tckn", "document_no", "gross_total", "vat_total", "payment_method", "bookkeeping_status"],
};

const REVIEW_LABELS = {
  bank: "Banka hareketi",
  z: "Z raporu",
  receipt: "Fiş / gider belgesi",
};

async function api(path, options = {}) {
  const timeoutMs = options.timeoutMs || 90000;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
    signal: controller.signal,
  });
  clearTimeout(timeout);
  if (!response.ok) {
    const err = await response.json().catch(() => ({ error: response.statusText || `HTTP ${response.status}` }));
    if (response.status === 413) {
      throw new Error("Dosya çok büyük. Büyük PDF yükleme için doğrudan Supabase yükleme akışı gerekiyor.");
    }
    throw new Error(err.error || response.statusText || `HTTP ${response.status}`);
  }
  return response.json();
}

async function refresh() {
  state = await api("/api/state");
  reconcileSelectedClient();
  renderCounts();
  renderClientList();
  renderSelectedClient();
  renderShell();
  renderReview();
  renderDataTable();
}

function reconcileSelectedClient() {
  if (!selectedClientId) return;
  const exists = state.clients.some((client) => String(client.id) === String(selectedClientId));
  if (!exists) {
    selectedClientId = "";
    localStorage.removeItem("maliyardimci:selectedClientId");
  }
}

function selectedClient() {
  if (!state || !selectedClientId) return null;
  return state.clients.find((client) => String(client.id) === String(selectedClientId)) || null;
}

function currentPeriod() {
  return $("#workspace-period")?.value.trim() || "2026-06";
}

function setSelectedClient(clientId, shouldScroll = true) {
  selectedClientId = clientId ? String(clientId) : "";
  if (selectedClientId) {
    localStorage.setItem("maliyardimci:selectedClientId", selectedClientId);
  } else {
    localStorage.removeItem("maliyardimci:selectedClientId");
  }
  activeReviewItem = null;
  activeView = "library";
  renderClientList();
  renderSelectedClient();
  renderShell();
  renderReview();
  renderDataTable();
  if (!selectedClient()) {
    if (shouldScroll) $("#client-gate")?.scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  switchView("library", false);
  if (shouldScroll) $("#client-workspace")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderShell() {
  const hasClient = Boolean(selectedClient());
  $("#client-gate")?.classList.toggle("is-hidden", hasClient);
  $("#client-workspace")?.classList.toggle("is-hidden", !hasClient);
  if (!hasClient) return;
  syncContextFields();
}

function renderCounts() {
  const target = $("#counts");
  if (!target || !state) return;
  const counts = state.counts;
  target.innerHTML = [
    ["Mükellef", counts.clients],
    ["Dosya", counts.documents],
    ["Banka", counts.bank],
    ["Z raporu", counts.z_reports],
    ["Fiş", counts.receipts],
    ["Kontrol", counts.review],
  ]
    .map(([label, value]) => `<div class="count"><strong>${escapeHtml(value)}</strong><span>${escapeHtml(label)}</span></div>`)
    .join("");

  const aiStatus = $("#ai-status");
  if (aiStatus) {
    const storage = state.storage?.provider === "supabase" ? "Supabase" : "yerel kayıt";
    aiStatus.textContent =
      state.ai?.provider === "gemini"
        ? `Belge okuma: Gemini (${state.ai.model}) · Veri: ${storage}`
        : `Belge okuma: yerel okuma · Veri: ${storage}`;
  }
}

function renderClientList() {
  const target = $("#client-list");
  if (!target || !state) return;
  if (!state.clients.length) {
    target.innerHTML = '<div class="empty-state">Henüz mükellef yok. İlk deneme için yeni mükellef oluştur.</div>';
    return;
  }
  target.innerHTML = state.clients
    .map((client) => {
      const isActive = String(client.id) === String(selectedClientId);
      return `
        <button type="button" class="client-card ${isActive ? "active" : ""}" data-client-id="${escapeHtml(client.id)}">
          <span>${escapeHtml(client.alias || "Mükellef")}</span>
          <strong>${escapeHtml(client.name)}</strong>
          <small>${isActive ? "Seçili mükellef" : "Kütüphaneyi aç"}</small>
        </button>
      `;
    })
    .join("");
  $$(".client-card").forEach((button) => {
    button.addEventListener("click", () => setSelectedClient(button.dataset.clientId));
  });
}

function renderSelectedClient() {
  const client = selectedClient();
  const name = $("#selected-client-name");
  const alias = $("#selected-client-alias");
  if (name) name.textContent = client ? client.name : "Mükellef seçilmedi";
  if (alias) alias.textContent = client ? client.alias || "Kısa ad yok" : "Dosyaları görmek için mükellef seç.";
  syncContextFields();
}

function syncContextFields() {
  const client = selectedClient();
  const clientId = client ? String(client.id) : "";
  const period = currentPeriod();
  $("#upload-client-id") && ($("#upload-client-id").value = clientId);
  $("#rule-client-id") && ($("#rule-client-id").value = clientId);
  $("#export-client") && ($("#export-client").value = clientId);
  $("#upload-period") && ($("#upload-period").value = period);
  $("#export-period") && ($("#export-period").value = period);
}

function filteredRows(rows) {
  const client = selectedClient();
  if (!client) return [];
  const clientId = String(client.id);
  const period = currentPeriod();
  return (rows || []).filter((row) => {
    const sameClient = row.client_id === undefined || String(row.client_id) === clientId;
    const samePeriod = row.period === undefined || String(row.period) === period;
    return sameClient && samePeriod;
  });
}

function filteredReviewItems() {
  return filteredRows(state?.review_items || []);
}

function renderReview() {
  const target = $("#review-table");
  if (!target || !state) return;
  const rows = filteredReviewItems();
  if (!rows.length) {
    target.innerHTML = '<div class="empty-state">Seçili dönem için kontrol bekleyen satır yok.</div>';
    if (!activeReviewItem && $("#review-detail")) {
      $("#review-detail").innerHTML = '<div class="empty-state">Kontrol için bir satır seç.</div>';
    }
    return;
  }
  const columns = ["item_type", "period", "title", "confidence", "detail"];
  const head = [...columns, "aksiyon"].map((col) => `<th>${escapeHtml(labelFor(col))}</th>`).join("");
  const body = rows
    .map((row) => {
      const isActive = activeReviewItem?.item_type === row.item_type && Number(activeReviewItem.id) === Number(row.id);
      const cells = columns.map((col) => `<td>${formatCell(col, row[col])}</td>`).join("");
      return `
        <tr class="${isActive ? "selected-row" : ""}">
          ${cells}
          <td>
            <button type="button" class="small-button review-open" data-item-type="${escapeHtml(row.item_type)}" data-id="${escapeHtml(row.id)}">
              Kontrol et
            </button>
          </td>
        </tr>
      `;
    })
    .join("");
  target.innerHTML = `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
  $$(".review-open").forEach((button) => {
    button.addEventListener("click", () => openReview(button.dataset.itemType, button.dataset.id));
  });
}

async function openReview(itemType, id) {
  activeReviewItem = { item_type: itemType, id: Number(id) };
  renderReview();
  const detail = $("#review-detail");
  detail.innerHTML = '<div class="empty-state">Kontrol ayrıntısı yükleniyor...</div>';
  try {
    const payload = await api(`/api/review-item?item_type=${encodeURIComponent(itemType)}&id=${encodeURIComponent(id)}`);
    renderReviewDetail(payload);
  } catch (error) {
    detail.innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
  }
}

function renderReviewDetail(payload) {
  const item = payload.item;
  const itemType = payload.item_type;
  const fields = REVIEW_FIELDS[itemType] || payload.editable_fields || [];
  const clientName = payload.client?.name || `Mükellef ${item.client_id}`;
  const documentName = item.source_file ? visibleDocumentTitle(item.source_file) : "Belge yok";
  const reviewTitle = item.source_file?.split(" - ")[0] || `${REVIEW_LABELS[itemType] || itemType} #${item.id}`;
  const rawText = item.raw_text || "";
  const feedback = payload.feedback || [];

  $("#review-detail").innerHTML = `
    <div class="review-detail-head">
      <div>
        <p class="eyebrow">Seçili kontrol</p>
        <h3>${escapeHtml(reviewTitle)}</h3>
      </div>
      ${formatCell("confidence", item.confidence)}
    </div>
    <div class="review-meta">
      <span>${escapeHtml(clientName)}</span>
      <span>${escapeHtml(item.period || "")}</span>
      <span>${escapeHtml(documentName)}</span>
    </div>
    <form id="review-form" class="review-form" data-item-type="${escapeHtml(itemType)}" data-id="${escapeHtml(item.id)}">
      <div class="review-field-grid">
        ${fields.map((field) => renderReviewField(field, item[field])).join("")}
      </div>
      <label>
        Açıklama notu
        <textarea id="review-note" rows="3" placeholder="Ali'nin yorumu veya senin düzeltme notun"></textarea>
      </label>
      <div class="review-actions">
        <button type="submit" class="primary" data-resolve="1" data-rating="dogru">Kaydet ve kontrolden çıkar</button>
        <button type="submit" class="secondary-light" data-resolve="0" data-rating="eksik">Kaydet, eksik kalsın</button>
        <button type="submit" class="secondary-light" data-resolve="0" data-rating="yanlis">Yanlış, tekrar bakılacak</button>
        <button type="submit" class="danger-light" data-resolve="1" data-rating="gereksiz">Gereksiz kapat</button>
      </div>
    </form>
    ${rawText ? `<details class="raw-text-box"><summary>Okunan metin</summary><pre>${escapeHtml(rawText)}</pre></details>` : ""}
    ${
      feedback.length
        ? `<div class="feedback-list"><strong>Son geri bildirim</strong>${feedback
            .map((entry) => `<p><b>${escapeHtml(VALUE_LABELS[entry.rating] || entry.rating)}</b> ${escapeHtml(entry.note || "")}</p>`)
            .join("")}</div>`
        : ""
    }
  `;
  $("#review-form").addEventListener("submit", submitReviewUpdate);
}

function renderReviewField(field, value) {
  const safeValue = value ?? "";
  if (field === "bookkeeping_status") {
    const options = ["uygun", "eksik", "okunamadi", "manuel_kontrol", "islenmez"];
    return `
      <label>
        ${escapeHtml(labelFor(field))}
        <select data-field="${escapeHtml(field)}">
          ${options
            .map((option) => `<option value="${escapeHtml(option)}" ${option === safeValue ? "selected" : ""}>${escapeHtml(VALUE_LABELS[option] || option)}</option>`)
            .join("")}
        </select>
      </label>
    `;
  }
  if (field === "vat_lines" || field === "payment_breakdown" || field === "description") {
    return `
      <label class="wide-field">
        ${escapeHtml(labelFor(field))}
        <textarea rows="3" data-field="${escapeHtml(field)}">${escapeHtml(safeValue)}</textarea>
      </label>
    `;
  }
  return `
    <label>
      ${escapeHtml(labelFor(field))}
      <input data-field="${escapeHtml(field)}" value="${escapeHtml(safeValue)}" />
    </label>
  `;
}

async function submitReviewUpdate(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const submitter = event.submitter;
  const values = {};
  form.querySelectorAll("[data-field]").forEach((field) => {
    values[field.dataset.field] = field.value;
  });
  const payload = {
    item_type: form.dataset.itemType,
    id: form.dataset.id,
    values,
    resolve: submitter?.dataset.resolve === "1",
    rating: submitter?.dataset.rating || "",
    note: $("#review-note")?.value || "",
  };
  await api("/api/review-item", { method: "POST", body: JSON.stringify(payload) });
  await refresh();
  if (payload.resolve) {
    activeReviewItem = null;
    $("#review-detail").innerHTML = '<div class="empty-state">Kontrol kapandı. Sıradaki satırı seç.</div>';
  } else {
    await openReview(payload.item_type, payload.id);
  }
}

function renderDataTable() {
  const target = $("#data-table");
  if (!target || !state) return;
  const datasets = {
    bank: filteredRows(state.bank_rows || []),
    z: filteredRows(state.z_reports || []),
    receipt: filteredRows(state.receipts || []),
    docs: filteredRows(state.recent_documents || []),
  };
  const preferred = {
    bank: ["id", "period", "bank_name", "date", "description", "debit", "credit", "counterparty_guess", "suggested_account_code", "confidence", "needs_review"],
    z: ["id", "period", "source_file", "report_date", "z_no", "gross_total", "device_brand", "confidence", "needs_review"],
    receipt: ["id", "period", "source_file", "receipt_date", "merchant_name", "vkn_tckn", "gross_total", "bookkeeping_status", "confidence", "needs_review"],
    docs: ["id", "period", "module", "original_name", "status", "warnings", "created_at"],
  };
  target.innerHTML = table(datasets[activeTab], preferred[activeTab], "Seçili dönem için kayıt yok.");
}

function table(rows, columns, emptyText) {
  if (!rows.length) return `<div class="empty-state">${emptyText}</div>`;
  const head = columns.map((col) => `<th>${escapeHtml(labelFor(col))}</th>`).join("");
  const body = rows
    .map((row) => `<tr>${columns.map((col) => `<td>${formatCell(col, row[col])}</td>`).join("")}</tr>`)
    .join("");
  return `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function formatCell(col, value) {
  if (col === "needs_review") {
    return value ? '<span class="badge review">kontrol</span>' : '<span class="badge ok">tamam</span>';
  }
  if (col === "confidence" && value !== undefined && value !== null && value !== "") {
    const number = Number(value);
    const cls = number < 0.65 ? "bad" : number < 0.8 ? "review" : "ok";
    return `<span class="badge ${cls}">${Math.round(number * 100)}%</span>`;
  }
  if (col === "item_type" || col === "module") return escapeHtml(TYPE_LABELS[value] || value || "");
  if (col === "source_file" || col === "title" || col === "original_name") return escapeHtml(visibleDocumentTitle(value));
  if (col === "bookkeeping_status" || col === "status") return escapeHtml(VALUE_LABELS[value] || value || "");
  if (col === "warnings") return escapeHtml(formatWarnings(value));
  return escapeHtml(value ?? "");
}

function labelFor(key) {
  if (key === "aksiyon") return "İşlem";
  return FIELD_LABELS[key] || key;
}

function visibleDocumentTitle(value) {
  if (!value) return "";
  const text = String(value);
  if (text.includes(" - ")) return text.split(" - ")[0];
  if (text.toLowerCase().endsWith(".pdf")) return "Yüklenen belge";
  return text;
}

function formatWarnings(value) {
  if (!value) return "";
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed.join(", ") : String(parsed);
  } catch {
    return String(value);
  }
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function showMessage(text) {
  const box = $("#message");
  if (!box) return;
  box.hidden = false;
  box.textContent = text;
}

function clearMessage() {
  const box = $("#message");
  if (!box) return;
  box.hidden = true;
  box.textContent = "";
}

function formatFileSize(bytes) {
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function switchView(viewName, shouldScroll = true) {
  if (!selectedClient()) {
    renderShell();
    if (shouldScroll) $("#client-gate")?.scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  activeView = viewName || "library";
  $$(".view").forEach((view) => view.classList.remove("active"));
  const target = $(`#view-${activeView}`) || $("#view-library");
  target.classList.add("active");
  $$(".workspace-tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === activeView);
  });
  if (shouldScroll) target.scrollIntoView({ behavior: "smooth", block: "start" });
}

function switchModule(moduleName) {
  const hidden = $('#upload-form input[name="module"]');
  if (hidden) hidden.value = moduleName;
  $$(".module-option").forEach((option) => {
    option.classList.toggle("active", option.querySelector("input").value === moduleName);
  });
  const bankField = $('#upload-form input[name="bank_name"]');
  const helper = $("#upload-helper");
  if (moduleName === "bank") {
    bankField.disabled = false;
    bankField.placeholder = "Akbank, Garanti...";
    helper.textContent = "Banka hareketi dosyasını yükle. Satırlar silinmez, belirsiz alanlar kontrole düşer.";
  } else if (moduleName === "z") {
    bankField.disabled = true;
    bankField.value = "";
    bankField.placeholder = "Z raporu için gerekmez";
    helper.textContent = "Z raporu fotoğrafı veya belge dosyası yükle. Eksik Z no, tarih veya toplam kontrole düşer.";
  } else {
    bankField.disabled = true;
    bankField.value = "";
    bankField.placeholder = "Fiş için gerekmez";
    helper.textContent = "Fiş veya gider belgesi yükle. Eksik VKN/TCKN veya düşük güven elle kontrol olarak işaretlenir.";
  }
}

async function fileToBase64(file) {
  const buffer = await file.arrayBuffer();
  let binary = "";
  const bytes = new Uint8Array(buffer);
  for (let i = 0; i < bytes.length; i += 1) binary += String.fromCharCode(bytes[i]);
  return btoa(binary);
}

$("#client-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  const created = await api("/api/clients", {
    method: "POST",
    body: JSON.stringify(Object.fromEntries(form.entries())),
  });
  event.target.reset();
  await refresh();
  setSelectedClient(created.id);
});

$("#upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const client = selectedClient();
  if (!client) {
    showMessage("Önce mükellef seç.");
    setSelectedClient("");
    return;
  }
  syncContextFields();
  const submitButton = event.submitter || event.target.querySelector('button[type="submit"]');
  const form = new FormData(event.target);
  const file = form.get("file");
  if (!file || !file.name) {
    showMessage("Lütfen bir dosya seç.");
    return;
  }
  if (file.size > MAX_FUNCTION_UPLOAD_BYTES && state.storage?.provider !== "supabase") {
    showMessage(`Bu dosya ${formatFileSize(file.size)}. Büyük PDF için Supabase kayıt sistemi gerekli.`);
    return;
  }
  submitButton.disabled = true;
  showMessage(file.size > MAX_FUNCTION_UPLOAD_BYTES ? "Büyük dosya Supabase'e yükleniyor..." : "Dosya işleniyor...");
  try {
    const basePayload = {
      client_id: client.id,
      period: currentPeriod(),
      module: form.get("module"),
      bank_name: form.get("bank_name"),
      filename: file.name,
    };
    let result;
    if (file.size > MAX_FUNCTION_UPLOAD_BYTES) {
      const uploadTarget = await api("/api/upload-url", { method: "POST", body: JSON.stringify(basePayload), timeoutMs: 30000 });
      const uploadResponse = await fetch(uploadTarget.upload_url, {
        method: "PUT",
        headers: { "Content-Type": file.type || "application/octet-stream" },
        body: file,
      });
      if (!uploadResponse.ok) {
        throw new Error(`Supabase yükleme başarısız oldu: HTTP ${uploadResponse.status}`);
      }
      showMessage("Dosya yüklendi. Gemini belgeyi işliyor...");
      result = await api("/api/process-stored-upload", {
        method: "POST",
        body: JSON.stringify({ ...basePayload, object_path: uploadTarget.object_path }),
        timeoutMs: 120000,
      });
    } else {
      result = await api("/api/upload", {
        method: "POST",
        body: JSON.stringify({ ...basePayload, content_base64: await fileToBase64(file) }),
        timeoutMs: 90000,
      });
    }
    showMessage(result.warnings?.length ? `İşlendi, uyarılar: ${result.warnings.join(", ")}` : "Dosya işlendi. Kütüphane ve kontrol kuyruğu güncellendi.");
    event.target.reset();
    $("#file-display").textContent = "Henüz dosya seçilmedi";
    switchModule("bank");
    await refresh();
    switchView(filteredReviewItems().length ? "review" : "library");
  } catch (error) {
    const message = error.name === "AbortError" ? "İşlem zaman aşımına uğradı. Daha küçük dosya veya doğrudan Supabase yükleme gerekli." : error.message;
    showMessage(message);
  } finally {
    submitButton.disabled = false;
  }
});

$("#rule-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const client = selectedClient();
  if (!client) {
    showMessage("Önce mükellef seç.");
    return;
  }
  syncContextFields();
  const form = new FormData(event.target);
  await api("/api/rules", {
    method: "POST",
    body: JSON.stringify(Object.fromEntries(form.entries())),
  });
  event.target.reset();
  syncContextFields();
  showMessage("Kural kaydedildi.");
  await refresh();
});

$("#refresh").addEventListener("click", refresh);
$("#library-refresh").addEventListener("click", refresh);

$("#change-client").addEventListener("click", () => {
  clearMessage();
  setSelectedClient("");
});

$("#workspace-period").addEventListener("change", () => {
  syncContextFields();
  activeReviewItem = null;
  renderReview();
  renderDataTable();
});

$("#export").addEventListener("click", () => {
  const client = selectedClient();
  const period = currentPeriod();
  if (!client || !period) {
    showMessage("Çıktı için mükellef ve dönem seç.");
    return;
  }
  window.location.href = `/api/export?client_id=${encodeURIComponent(client.id)}&period=${encodeURIComponent(period)}`;
});

$("#upload-file").addEventListener("change", (event) => {
  const count = event.target.files?.length || 0;
  $("#file-display").textContent = count ? `${count} dosya seçildi` : "Henüz dosya seçilmedi";
});

$$(".workspace-tab").forEach((button) => {
  button.addEventListener("click", () => switchView(button.dataset.view));
});

$$(".module-option input").forEach((input) => {
  input.addEventListener("change", () => switchModule(input.value));
});

$$(".tab").forEach((button) => {
  button.addEventListener("click", () => {
    $$(".tab").forEach((tab) => tab.classList.remove("active"));
    button.classList.add("active");
    activeTab = button.dataset.tab;
    renderDataTable();
  });
});

refresh().catch((error) => showMessage(error.message));
