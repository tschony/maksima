let state = null;
let activeTab = "bank";
let activeView = "library";
let activeReviewItem = null;
let selectedClientId = localStorage.getItem("maliyardimci:selectedClientId") || "";
let activeLibraryMonth = "";
let librarySearchTerm = "";
let libraryReviewFilter = "all";
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
  activeLibraryMonth = "";
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
    const providerText =
      state.ai?.provider === "openai"
        ? `ChatGPT (${state.ai.model})`
        : state.ai?.provider === "gemini"
          ? `Gemini (${state.ai.model})`
          : "yerel okuma";
    aiStatus.textContent = `Belge okuma: ${providerText} · Veri: ${storage}`;
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
  const baseRows = libraryRows(activeTab);
  const monthRows = activeLibraryMonth ? baseRows.filter((row) => rowMonth(row, activeTab) === activeLibraryMonth) : baseRows;
  const visibleRows = activeLibraryMonth ? applyLibraryFilters(monthRows, activeTab) : monthRows;
  renderLibrarySummary(visibleRows);
  renderLibraryControls();
  if (!activeLibraryMonth) {
    target.innerHTML = renderMonthFolders(baseRows);
    bindMonthFolders();
    return;
  }
  renderLibraryTitle(visibleRows.length);
  target.innerHTML = renderLibraryDetailTable(visibleRows, activeTab);
  bindLibraryActions();
}

function libraryRows(tabName) {
  const datasets = {
    bank: clientRows(state.bank_rows || []),
    z: clientRows(state.z_reports || []),
    receipt: clientRows(state.receipts || []),
    docs: clientRows(state.recent_documents || []),
  };
  return datasets[tabName] || [];
}

function clientRows(rows) {
  const client = selectedClient();
  if (!client) return [];
  const clientId = String(client.id);
  return (rows || []).filter((row) => row.client_id === undefined || String(row.client_id) === clientId);
}

function renderLibrarySummary(rows) {
  const target = $("#library-summary");
  if (!target) return;
  const summary = rows.reduce(
    (acc, row) => {
      acc.count += 1;
      acc.total += rowGrossTotal(row, activeTab);
      acc.vat += rowVatTotal(row, activeTab);
      return acc;
    },
    { count: 0, total: 0, vat: 0 },
  );
  target.innerHTML = [
    ["Kayıt sayısı", String(summary.count)],
    ["Toplam tutar", formatMoney(summary.total)],
    ["Toplam KDV", formatMoney(summary.vat)],
  ]
    .map(([label, value]) => `<div class="summary-card"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`)
    .join("");
}

function renderLibraryControls() {
  const filter = $("#library-filter");
  const title = $("#library-title-row");
  if (!filter || !title) return;
  filter.hidden = !activeLibraryMonth;
  if (!activeLibraryMonth) {
    title.innerHTML = '<div class="folder-hint">Önce ay klasörünü seç. Kayıtlar gerçek belge tarihine göre gruplanır.</div>';
  }
}

function renderLibraryTitle(rowCount) {
  const target = $("#library-title-row");
  if (!target) return;
  target.innerHTML = `
    <div class="library-detail-title">
      <div>
        <span>${escapeHtml(monthLabel(activeLibraryMonth))}</span>
        <strong>${escapeHtml(TAB_LABELS[activeTab] || "Kayıtlar")}</strong>
      </div>
      <small>${escapeHtml(rowCount)} satır gösteriliyor</small>
    </div>
  `;
}

function renderMonthFolders(rows) {
  const groups = groupRowsByMonth(rows, activeTab);
  if (!groups.length) return '<div class="empty-state">Bu bölüm için kayıt yok.</div>';
  return `
    <div class="month-grid">
      ${groups
        .map(
          (group) => `
            <button type="button" class="month-folder" data-month="${escapeHtml(group.month)}">
              <span>${escapeHtml(group.month)}</span>
              <strong>${escapeHtml(monthLabel(group.month))}</strong>
              <small>${escapeHtml(group.rows.length)} kayıt · ${escapeHtml(formatMoney(group.total))} · KDV ${escapeHtml(formatMoney(group.vat))}</small>
            </button>
          `,
        )
        .join("")}
    </div>
  `;
}

function groupRowsByMonth(rows, tabName) {
  const groups = new Map();
  rows.forEach((row) => {
    const month = rowMonth(row, tabName) || "Tarihsiz";
    if (!groups.has(month)) groups.set(month, []);
    groups.get(month).push(row);
  });
  return Array.from(groups.entries())
    .map(([month, groupRows]) => ({
      month,
      rows: groupRows,
      total: groupRows.reduce((sum, row) => sum + rowGrossTotal(row, tabName), 0),
      vat: groupRows.reduce((sum, row) => sum + rowVatTotal(row, tabName), 0),
    }))
    .sort((a, b) => String(b.month).localeCompare(String(a.month)));
}

function bindMonthFolders() {
  $$(".month-folder").forEach((button) => {
    button.addEventListener("click", () => {
      activeLibraryMonth = button.dataset.month || "";
      if (/^\d{4}-\d{2}$/.test(activeLibraryMonth) && $("#workspace-period")) {
        $("#workspace-period").value = activeLibraryMonth;
        syncContextFields();
      }
      renderDataTable();
    });
  });
}

const TAB_LABELS = {
  bank: "Banka hareketleri",
  z: "Z raporları",
  receipt: "Fişler",
  docs: "Yüklemeler",
};

const LIBRARY_COLUMNS = {
  bank: ["date", "bank_name", "description", "debit", "credit", "suggested_account_code", "confidence", "needs_review"],
  z: ["report_date", "source_file", "z_no", "gross_total", "vat_lines", "confidence", "needs_review"],
  receipt: ["receipt_date", "merchant_name", "vkn_tckn", "document_no", "gross_total", "vat_total", "bookkeeping_status", "confidence", "needs_review"],
  docs: ["period", "module", "original_name", "status", "warnings", "created_at"],
};

function renderLibraryDetailTable(rows, tabName) {
  if (!rows.length) return '<div class="empty-state">Bu filtreye uyan kayıt yok.</div>';
  const columns = LIBRARY_COLUMNS[tabName] || [];
  const head = [...columns, "aksiyon"].map((col) => `<th>${escapeHtml(labelFor(col))}</th>`).join("");
  const body = rows
    .map((row) => `<tr>${columns.map((col) => `<td>${formatCell(col, row[col])}</td>`).join("")}<td>${renderLibraryActions(row, tabName)}</td></tr>`)
    .join("");
  return `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function renderLibraryActions(row, tabName) {
  const documentId = tabName === "docs" ? row.id : row.document_id;
  const controls = [];
  if (documentId) {
    controls.push(`<button type="button" class="tiny-button document-open" data-document-id="${escapeHtml(documentId)}">Belgeyi aç</button>`);
  }
  if (tabName !== "docs") {
    controls.push(`<button type="button" class="tiny-button library-review-open" data-item-type="${escapeHtml(tabName)}" data-id="${escapeHtml(row.id)}">Kontrol et</button>`);
    controls.push(`<button type="button" class="tiny-button danger library-item-delete" data-item-type="${escapeHtml(tabName)}" data-id="${escapeHtml(row.id)}">Sil</button>`);
  } else if (documentNeedsReview(row.id)) {
    controls.push('<button type="button" class="tiny-button library-review-tab">Kontrole git</button>');
  }
  if (tabName === "docs" && documentId) {
    controls.push(`<button type="button" class="tiny-button danger document-delete" data-document-id="${escapeHtml(documentId)}">Sil</button>`);
  }
  return `<div class="row-actions">${controls.join("")}</div>`;
}

function bindLibraryActions() {
  $$(".document-open").forEach((button) => {
    button.addEventListener("click", () => openDocument(button.dataset.documentId));
  });
  $$(".library-review-open").forEach((button) => {
    button.addEventListener("click", async () => {
      switchView("review");
      await openReview(button.dataset.itemType, button.dataset.id);
    });
  });
  $$(".library-review-tab").forEach((button) => {
    button.addEventListener("click", () => switchView("review"));
  });
  $$(".library-item-delete").forEach((button) => {
    button.addEventListener("click", () => deleteLibraryItem(button.dataset.itemType, button.dataset.id, button));
  });
  $$(".document-delete").forEach((button) => {
    button.addEventListener("click", () => deleteDocument(button.dataset.documentId, button));
  });
}

function openDocument(documentId) {
  const client = selectedClient();
  if (!client || !documentId) return;
  window.open(`/api/document?document_id=${encodeURIComponent(documentId)}&client_id=${encodeURIComponent(client.id)}`, "_blank", "noopener");
}

async function deleteLibraryItem(itemType, itemId, button = null) {
  const client = selectedClient();
  if (!client || !itemType || !itemId) return;
  const label = TYPE_LABELS[itemType] || "Kayıt";
  if (!window.confirm(`${label} kaydı silinsin mi? Bu işlem geri alınamaz.`)) return;
  if (button) button.disabled = true;
  try {
    await api("/api/delete-item", {
      method: "POST",
      body: JSON.stringify({ client_id: client.id, item_type: itemType, id: itemId }),
    });
    showMessage("Kayıt silindi.");
    await refresh();
    renderDataTable();
  } catch (error) {
    showMessage(`Kayıt silinemedi: ${error.message}`);
  } finally {
    if (button?.isConnected) button.disabled = false;
  }
}

async function deleteDocument(documentId, button = null) {
  const client = selectedClient();
  if (!client || !documentId) return;
  if (!window.confirm("Bu yükleme ve ona bağlı bütün satırlar silinsin mi? Bu işlem geri alınamaz.")) return;
  if (button) button.disabled = true;
  try {
    const result = await api("/api/delete-document", {
      method: "POST",
      body: JSON.stringify({ client_id: client.id, document_id: documentId }),
    });
    showMessage(result.storage_warning || "Belge ve bağlı kayıtlar silindi.");
    await refresh();
    renderDataTable();
  } catch (error) {
    showMessage(`Belge silinemedi: ${error.message}`);
  } finally {
    if (button?.isConnected) button.disabled = false;
  }
}

function applyLibraryFilters(rows, tabName) {
  const search = librarySearchTerm.trim().toLocaleLowerCase("tr-TR");
  return rows.filter((row) => {
    if (libraryReviewFilter === "review" && !rowNeedsReview(row, tabName)) return false;
    if (libraryReviewFilter === "done" && rowNeedsReview(row, tabName)) return false;
    if (!search) return true;
    return Object.values(row).join(" ").toLocaleLowerCase("tr-TR").includes(search);
  });
}

function rowMonth(row, tabName) {
  if (tabName === "bank") return parseYearMonth(row.date) || parseYearMonth(row.period);
  if (tabName === "z") return parseYearMonth(row.report_date) || parseYearMonth(row.period);
  if (tabName === "receipt") return parseYearMonth(row.receipt_date) || parseYearMonth(row.period);
  if (tabName === "docs") return documentMonth(row);
  return parseYearMonth(row.period);
}

function documentMonth(documentRow) {
  const related = relatedDocumentRows(documentRow.id);
  const month = related.map((entry) => rowMonth(entry.row, entry.tab)).find(Boolean);
  return month || parseYearMonth(documentRow.period) || "Tarihsiz";
}

function relatedDocumentRows(documentId) {
  const id = String(documentId);
  return [
    ...clientRows(state.bank_rows || []).filter((row) => String(row.document_id) === id).map((row) => ({ tab: "bank", row })),
    ...clientRows(state.z_reports || []).filter((row) => String(row.document_id) === id).map((row) => ({ tab: "z", row })),
    ...clientRows(state.receipts || []).filter((row) => String(row.document_id) === id).map((row) => ({ tab: "receipt", row })),
  ];
}

function documentNeedsReview(documentId) {
  return relatedDocumentRows(documentId).some((entry) => rowNeedsReview(entry.row, entry.tab));
}

function rowNeedsReview(row, tabName) {
  if (tabName === "docs") return row.status === "failed" || documentNeedsReview(row.id);
  return Boolean(row.needs_review);
}

function rowGrossTotal(row, tabName) {
  if (tabName === "bank") return Math.abs(parseMoney(row.debit)) + Math.abs(parseMoney(row.credit));
  if (tabName === "z" || tabName === "receipt") return parseMoney(row.gross_total);
  if (tabName === "docs") return relatedDocumentRows(row.id).reduce((sum, entry) => sum + rowGrossTotal(entry.row, entry.tab), 0);
  return 0;
}

function rowVatTotal(row, tabName) {
  if (tabName === "receipt") return parseMoney(row.vat_total);
  if (tabName === "z") return parseVatLines(row.vat_lines);
  if (tabName === "docs") return relatedDocumentRows(row.id).reduce((sum, entry) => sum + rowVatTotal(entry.row, entry.tab), 0);
  return 0;
}

function parseYearMonth(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  let match = text.match(/^(\d{4})[-/.](\d{1,2})/);
  if (match) return `${match[1]}-${match[2].padStart(2, "0")}`;
  match = text.match(/^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})/);
  if (match) return `${match[3]}-${match[2].padStart(2, "0")}`;
  match = text.match(/^(\d{4})-(\d{2})$/);
  if (match) return text;
  return "";
}

function monthLabel(value) {
  if (!/^\d{4}-\d{2}$/.test(value)) return value || "Tarihsiz";
  const [year, month] = value.split("-").map(Number);
  const date = new Date(year, month - 1, 1);
  const label = new Intl.DateTimeFormat("tr-TR", { month: "long", year: "numeric" }).format(date);
  return label.charAt(0).toLocaleUpperCase("tr-TR") + label.slice(1);
}

function parseMoney(value) {
  let text = String(value ?? "").trim();
  if (!text) return 0;
  text = text.replace(/[^\d,.\-]/g, "");
  if (!text || text === "-" || text === "," || text === ".") return 0;
  const lastComma = text.lastIndexOf(",");
  const lastDot = text.lastIndexOf(".");
  if (lastComma > -1 && lastDot > -1) {
    text = lastComma > lastDot ? text.replace(/\./g, "").replace(",", ".") : text.replace(/,/g, "");
  } else if (lastComma > -1) {
    text = text.replace(/\./g, "").replace(",", ".");
  }
  const parsed = Number(text);
  return Number.isFinite(parsed) ? parsed : 0;
}

function parseVatLines(value) {
  if (!value) return 0;
  try {
    const parsed = JSON.parse(value);
    return sumVatNode(parsed);
  } catch {
    const matches = String(value).match(/-?\d[\d.,]*/g) || [];
    return matches.reduce((sum, item) => sum + parseMoney(item), 0);
  }
}

function sumVatNode(node) {
  if (Array.isArray(node)) return node.reduce((sum, item) => sum + sumVatNode(item), 0);
  if (node && typeof node === "object") {
    return Object.entries(node).reduce((sum, [key, val]) => {
      if (/amount|tutar|kdv|vat|tax/i.test(key)) return sum + parseMoney(val);
      if (typeof val === "object") return sum + sumVatNode(val);
      return sum;
    }, 0);
  }
  return 0;
}

function formatMoney(value) {
  return `${new Intl.NumberFormat("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value || 0)} ₺`;
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

function friendlyUploadMessage(message) {
  const text = String(message || "").trim();
  if (!text) return "";
  if (/OpenAI anahtarı|invalid_api_key|401/i.test(text)) {
    return "OpenAI anahtarı geçersiz veya eksik.";
  }
  if (/OpenAI.*(429|rate|limit|kullanım sınırı)/i.test(text)) {
    return "OpenAI kullanım sınırı geçici olarak doldu. Birkaç dakika sonra tekrar dene.";
  }
  if (/OpenAI.*(500|502|503|504|geçici olarak yanıt)/i.test(text)) {
    return "OpenAI geçici olarak yanıt veremedi. Birkaç dakika sonra tekrar dene.";
  }
  if (/Gemini.*(503|UNAVAILABLE|high demand)|Gemini şu anda yoğun/i.test(text)) {
    return "Gemini şu anda yoğun. Birkaç dakika sonra tekrar dene.";
  }
  if (/Gemini.*(429|RESOURCE_EXHAUSTED)/i.test(text)) {
    return "Gemini kullanım sınırı geçici olarak doldu. Birkaç dakika sonra tekrar dene.";
  }
  if (text.includes('{"error"') || text.includes('"error"')) {
    return "Belge okuma servisi geçici olarak yanıt veremedi. Birkaç dakika sonra tekrar dene.";
  }
  return text.length > 260 ? `${text.slice(0, 260)}...` : text;
}

function uniqueValues(values) {
  return Array.from(new Set(values.map(friendlyUploadMessage).filter(Boolean)));
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

async function processUploadFile(file, basePayload) {
  if (file.size > MAX_FUNCTION_UPLOAD_BYTES && state.storage?.provider !== "supabase") {
    throw new Error(`Bu dosya ${formatFileSize(file.size)}. Büyük PDF için Supabase kayıt sistemi gerekli.`);
  }
  const payload = { ...basePayload, filename: file.name };
  if (file.size > MAX_FUNCTION_UPLOAD_BYTES) {
    const uploadTarget = await api("/api/upload-url", { method: "POST", body: JSON.stringify(payload), timeoutMs: 30000 });
    const uploadResponse = await fetch(uploadTarget.upload_url, {
      method: "PUT",
      headers: { "Content-Type": file.type || "application/octet-stream" },
      body: file,
    });
    if (!uploadResponse.ok) {
      throw new Error(`Supabase yükleme başarısız oldu: HTTP ${uploadResponse.status}`);
    }
    return api("/api/process-stored-upload", {
      method: "POST",
      body: JSON.stringify({ ...payload, object_path: uploadTarget.object_path }),
      timeoutMs: 120000,
    });
  }
  return api("/api/upload", {
    method: "POST",
    body: JSON.stringify({ ...payload, content_base64: await fileToBase64(file) }),
    timeoutMs: 90000,
  });
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
  const files = form.getAll("file").filter((file) => file && file.name);
  if (!files.length) {
    showMessage("Lütfen en az bir dosya seç.");
    return;
  }
  const moduleName = form.get("module");
  const bankName = form.get("bank_name");
  submitButton.disabled = true;
  $("#upload-file").disabled = true;
  showMessage(`${files.length} dosya işleniyor...`);
  try {
    const basePayload = {
      client_id: client.id,
      period: currentPeriod(),
      module: moduleName,
      bank_name: bankName,
    };
    const results = [];
    for (const [index, file] of files.entries()) {
      showMessage(`${index + 1}/${files.length} işleniyor: ${file.name}`);
      try {
        const result = await processUploadFile(file, basePayload);
        results.push({ file: file.name, ok: true, warnings: (result.warnings || []).map(friendlyUploadMessage) });
      } catch (error) {
        const message = error.name === "AbortError" ? "İşlem zaman aşımına uğradı." : friendlyUploadMessage(error.message);
        results.push({ file: file.name, ok: false, warnings: [message] });
      }
    }
    const okCount = results.filter((result) => result.ok).length;
    const failed = results.filter((result) => !result.ok);
    const warnings = uniqueValues(results.flatMap((result) => result.warnings));
    const summary = failed.length
      ? `${okCount}/${files.length} dosya işlendi. İşlenemeyen dosyalar: ${failed.map((result) => result.file).join(", ")}`
      : `${okCount} dosya işlendi. Kütüphane ve kontrol kuyruğu güncellendi.`;
    showMessage(warnings.length ? `${summary} Not: ${warnings.join(" ")}` : summary);
    event.target.reset();
    $("#file-display").textContent = "Henüz dosya seçilmedi";
    const selectedRadio = $(`.module-option input[value="${moduleName}"]`);
    if (selectedRadio) selectedRadio.checked = true;
    switchModule(moduleName);
    await refresh();
    switchView(filteredReviewItems().length ? "review" : "library");
  } catch (error) {
    const message =
      error.name === "AbortError"
        ? "İşlem zaman aşımına uğradı. Daha küçük dosya veya doğrudan Supabase yükleme gerekli."
        : friendlyUploadMessage(error.message);
    showMessage(message);
  } finally {
    submitButton.disabled = false;
    $("#upload-file").disabled = false;
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

$("#library-search").addEventListener("input", (event) => {
  librarySearchTerm = event.target.value || "";
  renderDataTable();
});

$("#library-review-filter").addEventListener("change", (event) => {
  libraryReviewFilter = event.target.value || "all";
  renderDataTable();
});

$("#library-back").addEventListener("click", () => {
  activeLibraryMonth = "";
  librarySearchTerm = "";
  libraryReviewFilter = "all";
  $("#library-search").value = "";
  $("#library-review-filter").value = "all";
  renderDataTable();
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
    activeLibraryMonth = "";
    librarySearchTerm = "";
    libraryReviewFilter = "all";
    $("#library-search").value = "";
    $("#library-review-filter").value = "all";
    renderDataTable();
  });
});

refresh().catch((error) => showMessage(error.message));
