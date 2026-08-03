const bridge = window.AstrBotPluginPage;

const state = {
  kbs: [],
  kbId: null,
  docs: [],
  page: 1,
  pageSize: 20,
  total: 0,
  search: "",
  selectedDocId: null,
  selectedBatch: new Set(), // 批量删除勾选的 doc_id
  mode: "view", // view | create
  loading: false,
};

const $ = (id) => document.getElementById(id);

function toast(msg, type = "ok") {
  const el = $("toast");
  el.textContent = msg;
  el.className = `toast ${type}`;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.add("hidden"), 3200);
}

function setStatus(text) {
  $("status-text").textContent = text;
}

function fmtSize(n) {
  const v = Number(n || 0);
  if (v < 1024) return `${v}B`;
  if (v < 1024 * 1024) return `${(v / 1024).toFixed(1)}KB`;
  return `${(v / 1024 / 1024).toFixed(2)}MB`;
}

function esc(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function apiGet(path, params = {}) {
  const res = await bridge.apiGet(`page/${path}`, params);
  return unwrap(res);
}

async function apiPost(path, body = {}) {
  const res = await bridge.apiPost(`page/${path}`, body);
  return unwrap(res);
}

function unwrap(res) {
  // bridge 可能已经解包，也可能原样返回
  if (res && res.status === "error") {
    throw new Error(res.message || "请求失败");
  }
  if (res && res.status === "ok" && Object.prototype.hasOwnProperty.call(res, "data")) {
    return res.data;
  }
  return res;
}

function openModal({ title, body, okText = "确认", danger = true }) {
  return new Promise((resolve) => {
    $("modal-title").textContent = title;
    $("modal-body").textContent = body;
    $("modal-ok").textContent = okText;
    $("modal-ok").className = danger ? "btn btn-danger" : "btn btn-primary";
    $("modal").classList.remove("hidden");

    const cleanup = (result) => {
      $("modal").classList.add("hidden");
      $("modal-ok").onclick = null;
      $("modal-cancel").onclick = null;
      resolve(result);
    };
    $("modal-ok").onclick = () => cleanup(true);
    $("modal-cancel").onclick = () => cleanup(false);
  });
}

function renderKbList() {
  const root = $("kb-list");
  if (!state.kbs.length) {
    root.innerHTML = `<div class="empty">还没有知识库</div>`;
    return;
  }
  root.innerHTML = state.kbs
    .map((kb) => {
      const active = kb.kb_id === state.kbId ? "active" : "";
      return `
        <button class="item ${active}" data-kb="${esc(kb.kb_id)}" type="button">
          <div class="item-title">${esc(kb.emoji || "📘")} ${esc(kb.kb_name || "未命名")}</div>
          <div class="item-sub">文档 ${esc(kb.doc_count || 0)} · 块 ${esc(kb.chunk_count || 0)}</div>
        </button>`;
    })
    .join("");

  root.querySelectorAll("[data-kb]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      state.kbId = btn.getAttribute("data-kb");
      state.page = 1;
      state.selectedDocId = null;
      state.selectedBatch.clear();
      updateBatchButton();
      state.mode = "view";
      clearEditor();
      renderKbList();
      await loadDocuments();
    });
  });
}

function renderDocs() {
  const root = $("doc-list");
  const kb = state.kbs.find((x) => x.kb_id === state.kbId);
  $("doc-panel-title").textContent = kb ? `${kb.emoji || "📘"} ${kb.kb_name}` : "文档列表";
  $("doc-panel-meta").textContent = kb
    ? `共 ${state.total} 个文档 · 第 ${state.page}/${Math.max(1, Math.ceil(state.total / state.pageSize))} 页`
    : "先选一个知识库";

  if (!state.kbId) {
    root.innerHTML = `<div class="empty">先从左侧选择知识库</div>`;
    $("page-info").textContent = "-";
    return;
  }
  if (!state.docs.length) {
    root.innerHTML = `<div class="empty">没有文档</div>`;
  } else {
    root.innerHTML = state.docs
      .map((d) => {
        const active = d.doc_id === state.selectedDocId ? "active" : "";
        const checked = state.selectedBatch.has(d.doc_id) ? "checked" : "";
        return `
          <div class="doc-row ${active}" data-doc="${esc(d.doc_id)}">
            <input class="doc-check" type="checkbox" data-check="${esc(d.doc_id)}" ${checked} title="勾选后批量删除" />
            <button class="item grow" type="button">
              <div class="item-title">${esc(d.doc_name || d.doc_id)}</div>
              <div class="item-sub">${esc(d.file_type || "-")} · ${fmtSize(d.file_size)} · 块 ${esc(d.chunk_count || 0)}</div>
            </button>
          </div>`;
      })
      .join("");
    root.querySelectorAll("[data-check]").forEach((cb) => {
      cb.addEventListener("change", (ev) => {
        const docId = cb.getAttribute("data-check");
        if (ev.target.checked) state.selectedBatch.add(docId);
        else state.selectedBatch.delete(docId);
        updateBatchButton();
      });
    });
    root.querySelectorAll(".doc-row").forEach((row) => {
      row.addEventListener("click", async (ev) => {
        if (ev.target.classList.contains("doc-check")) return;
        const docId = row.getAttribute("data-doc");
        await openDocument(docId);
      });
    });
  }

  const totalPages = Math.max(1, Math.ceil((state.total || 0) / state.pageSize));
  $("page-info").textContent = `${state.page} / ${totalPages}`;
  $("btn-prev").disabled = state.page <= 1;
  $("btn-next").disabled = state.page >= totalPages;

  // 全选本页：当前页文档全部被勾选时自动打勾
  const checkAll = $("check-all");
  if (checkAll) {
    const pageIds = state.docs.map((d) => d.doc_id);
    const selectedOnPage = pageIds.filter((id) => state.selectedBatch.has(id)).length;
    checkAll.checked = pageIds.length > 0 && selectedOnPage === pageIds.length;
    checkAll.indeterminate = selectedOnPage > 0 && selectedOnPage < pageIds.length;
  }
}

function clearEditor() {
  $("editor-title").textContent = "文档编辑";
  $("editor-meta").textContent = "选择左侧文档进行查看 / 修改 / 删除";
  $("doc-name").value = "";
  $("doc-content").value = "";
  $("doc-name").disabled = true;
  $("doc-content").disabled = true;
  $("btn-save").disabled = true;
  $("btn-delete").disabled = true;
  $("btn-download").disabled = true;
  $("editor-hint").textContent =
    "提示：原生知识库没有“原地改文件”，保存 = 重建文档。请确认后再保存。";
}

function enableEditor({ title, meta, name, content, canDelete }) {
  $("editor-title").textContent = title;
  $("editor-meta").textContent = meta;
  $("doc-name").value = name || "";
  $("doc-content").value = content || "";
  $("doc-name").disabled = false;
  $("doc-content").disabled = false;
  $("btn-save").disabled = false;
  $("btn-delete").disabled = !canDelete;
  $("btn-download").disabled = !canDelete;
}

async function loadKbs() {
  setStatus("加载知识库…");
  const data = await apiGet("kbs");
  state.kbs = data.items || [];
  renderKbList();
  if (!state.kbId && state.kbs.length) {
    state.kbId = state.kbs[0].kb_id;
    renderKbList();
    await loadDocuments();
  } else if (state.kbId) {
    await loadDocuments();
  } else {
    renderDocs();
  }
  setStatus(`已加载 ${state.kbs.length} 个知识库`);
}

async function loadDocuments() {
  if (!state.kbId) {
    state.docs = [];
    state.total = 0;
    renderDocs();
    return;
  }
  setStatus("加载文档…");
  const data = await apiGet("documents", {
    kb_id: state.kbId,
    page: String(state.page),
    page_size: String(state.pageSize),
    search: state.search || "",
  });
  state.docs = data.items || [];
  state.total = data.total || 0;
  // 同步知识库统计
  if (data.kb) {
    const idx = state.kbs.findIndex((k) => k.kb_id === data.kb.kb_id);
    if (idx >= 0) state.kbs[idx] = { ...state.kbs[idx], ...data.kb };
    renderKbList();
  }
  renderDocs();
  setStatus(`文档 ${state.total} 个`);
}

async function openDocument(docId) {
  if (!state.kbId || !docId) return;
  state.mode = "view";
  state.selectedDocId = docId;
  renderDocs();
  setStatus("读取文档内容…");
  try {
    const data = await apiGet("document/content", {
      kb_id: state.kbId,
      doc_id: docId,
    });
    const doc = data.document || {};
    enableEditor({
      title: "编辑文档",
      meta: `ID: ${doc.doc_id || docId} · 块 ${data.chunk_count ?? doc.chunk_count ?? 0}`,
      name: doc.doc_name || "",
      content: data.content || "",
      canDelete: true,
    });
    if (data.note) $("editor-hint").textContent = data.note;
    setStatus("文档已加载");
  } catch (e) {
    toast(e.message || String(e), "err");
    setStatus("读取失败");
  }
}

async function saveDocument() {
  if (!state.kbId) {
    toast("请先选择知识库", "err");
    return;
  }
  const name = $("doc-name").value.trim();
  const content = $("doc-content").value;
  if (!name) {
    toast("文档名称不能为空", "err");
    return;
  }
  if (!content.trim()) {
    toast("正文不能为空", "err");
    return;
  }

  if (state.mode === "create") {
    const ok = await openModal({
      title: "确认新建文档",
      body: `将在当前知识库新建：\n${name}`,
      okText: "创建",
      danger: false,
    });
    if (!ok) return;
    setStatus("创建中…");
    try {
      const data = await apiPost("document/create", {
        kb_id: state.kbId,
        doc_name: name,
        content,
      });
      toast(data.message || "创建成功", "ok");
      state.selectedDocId = data.document?.doc_id || null;
      state.mode = "view";
      await loadDocuments();
      if (state.selectedDocId) await openDocument(state.selectedDocId);
    } catch (e) {
      toast(e.message || String(e), "err");
      setStatus("创建失败");
    }
    return;
  }

  if (!state.selectedDocId) {
    toast("请先选择文档", "err");
    return;
  }
  const ok = await openModal({
    title: "确认保存修改",
    body:
      "保存会删除旧文档并以新内容重新入库（重建向量索引）。\n" +
      `文档：${name}\nID：${state.selectedDocId}`,
    okText: "保存",
    danger: false,
  });
  if (!ok) return;

  setStatus("保存中（重建索引）…");
  try {
    const data = await apiPost("document/update", {
      kb_id: state.kbId,
      doc_id: state.selectedDocId,
      doc_name: name,
      content,
    });
    toast(data.message || "保存成功", "ok");
    state.selectedDocId = data.document?.doc_id || state.selectedDocId;
    await loadDocuments();
    if (state.selectedDocId) await openDocument(state.selectedDocId);
  } catch (e) {
    toast(e.message || String(e), "err");
    setStatus("保存失败");
  }
}

async function downloadDocument() {
  if (!state.kbId || !state.selectedDocId || state.mode === "create") {
    toast("请先选择要下载的文档", "err");
    return;
  }
  const name = $("doc-name").value.trim() || state.selectedDocId;
  setStatus("准备下载…");
  try {
    await bridge.download(
      "page/document/download",
      { kb_id: state.kbId, doc_id: state.selectedDocId },
      name
    );
    toast(`开始下载：${name}`, "ok");
    setStatus("下载完成");
  } catch (e) {
    toast(e.message || String(e), "err");
    setStatus("下载失败");
  }
}

async function deleteDocument() {
  if (!state.kbId || !state.selectedDocId || state.mode === "create") {
    toast("请先选择要删除的文档", "err");
    return;
  }
  const name = $("doc-name").value.trim() || state.selectedDocId;
  const ok = await openModal({
    title: "确认删除文档",
    body: `此操作不可恢复。\n知识库文档将被删除，向量索引一并清理。\n\n文件：${name}\nID：${state.selectedDocId}`,
    okText: "删除",
    danger: true,
  });
  if (!ok) return;
  setStatus("删除中…");
  try {
    const data = await apiPost("document/delete", {
      kb_id: state.kbId,
      doc_id: state.selectedDocId,
    });
    toast(data.message || "删除成功", "ok");
    state.selectedBatch.delete(state.selectedDocId);
    state.selectedDocId = null;
    updateBatchButton();
    clearEditor();
    await loadDocuments();
  } catch (e) {
    toast(e.message || String(e), "err");
    setStatus("删除失败");
  }
}

async function batchDownload() {
  if (!state.kbId) {
    toast("请先选择知识库", "err");
    return;
  }
  if (state.selectedBatch.size === 0) {
    toast("请先勾选要下载的文档", "err");
    return;
  }
  setStatus("打包下载中…");
  try {
    await bridge.download(
      "page/document/batch_download",
      {
        kb_id: state.kbId,
        doc_ids: [...state.selectedBatch].join(","),
      },
      `批量下载_${state.selectedBatch.size}个文档.zip`
    );
    toast(`已下载 ${state.selectedBatch.size} 个文档`, "ok");
    setStatus("下载完成");
  } catch (e) {
    toast(e.message || String(e), "err");
    setStatus("批量下载失败");
  }
}

function updateBatchButton() {
  const btn = $("btn-batch-delete");
  if (!btn) return;
  btn.disabled = state.selectedBatch.size === 0;
  btn.textContent = `批量删除${state.selectedBatch.size ? ` (${state.selectedBatch.size})` : ""}`;
  const btnDl = $("btn-batch-download");
  if (btnDl) {
    btnDl.disabled = state.selectedBatch.size === 0;
    btnDl.textContent = `批量下载${state.selectedBatch.size ? ` (${state.selectedBatch.size})` : ""}`;
  }
}

async function batchDelete() {
  if (!state.kbId) {
    toast("请先选择知识库", "err");
    return;
  }
  if (state.selectedBatch.size === 0) {
    toast("请先勾选要删除的文档", "err");
    return;
  }
  const names = state.docs
    .filter((d) => state.selectedBatch.has(d.doc_id))
    .map((d) => d.doc_name || d.doc_id);
  const preview = names.slice(0, 20).map((n) => `· ${n}`).join("\n");
  const ok = await openModal({
    title: `确认批量删除 ${state.selectedBatch.size} 个文档`,
    body:
      "此操作不可恢复，向量索引一并清理。\n\n" +
      preview +
      (names.length > 20 ? `\n… 等共 ${names.length} 个` : ""),
    okText: "全部删除",
    danger: true,
  });
  if (!ok) return;
  setStatus("批量删除中…");
  try {
    const data = await apiPost("document/batch_delete", {
      kb_id: state.kbId,
      doc_ids: [...state.selectedBatch],
    });
    const extra = data.fail_count ? `，失败 ${data.fail_count} 个` : "";
    toast(`${data.message || "删除成功"}${extra}`, data.fail_count ? "err" : "ok");
    state.selectedBatch.clear();
    state.selectedDocId = null;
    clearEditor();
    updateBatchButton();
    await loadDocuments();
  } catch (e) {
    toast(e.message || String(e), "err");
    setStatus("批量删除失败");
  }
}

function startCreate() {
  if (!state.kbId) {
    toast("请先选择知识库", "err");
    return;
  }
  state.mode = "create";
  state.selectedDocId = null;
  renderDocs();
  enableEditor({
    title: "新建文档",
    meta: "填写名称和正文后点击保存",
    name: "新文档.txt",
    content: "",
    canDelete: false,
  });
  $("editor-hint").textContent = "新建会直接写入知识库并建立向量索引。";
  $("doc-content").focus();
}

async function main() {
  try {
    await bridge.ready();
  } catch (e) {
    setStatus("Bridge 未就绪");
    toast("页面 Bridge 未就绪，请在 AstrBot WebUI 插件页打开", "err");
  }

  $("btn-refresh").addEventListener("click", () => loadKbs().catch((e) => toast(e.message, "err")));
  $("btn-create").addEventListener("click", startCreate);
  $("btn-save").addEventListener("click", () => saveDocument().catch((e) => toast(e.message, "err")));
  $("btn-download").addEventListener("click", () => downloadDocument().catch((e) => toast(e.message, "err")));
  $("btn-delete").addEventListener("click", () => deleteDocument().catch((e) => toast(e.message, "err")));
  $("btn-batch-download").addEventListener("click", () => batchDownload().catch((e) => toast(e.message, "err")));
  $("btn-batch-delete").addEventListener("click", () => batchDelete().catch((e) => toast(e.message, "err")));
  $("check-all").addEventListener("change", (ev) => {
    const checked = ev.target.checked;
    for (const d of state.docs) {
      if (checked) state.selectedBatch.add(d.doc_id);
      else state.selectedBatch.delete(d.doc_id);
    }
    updateBatchButton();
    renderDocs();
  });
  $("btn-search").addEventListener("click", async () => {
    state.search = $("search-input").value.trim();
    state.page = 1;
    await loadDocuments();
  });
  $("search-input").addEventListener("keydown", async (ev) => {
    if (ev.key === "Enter") {
      state.search = $("search-input").value.trim();
      state.page = 1;
      await loadDocuments();
    }
  });
  $("btn-prev").addEventListener("click", async () => {
    if (state.page > 1) {
      state.page -= 1;
      await loadDocuments();
    }
  });
  $("btn-next").addEventListener("click", async () => {
    const totalPages = Math.max(1, Math.ceil((state.total || 0) / state.pageSize));
    if (state.page < totalPages) {
      state.page += 1;
      await loadDocuments();
    }
  });

  clearEditor();
  try {
    await loadKbs();
  } catch (e) {
    toast(e.message || String(e), "err");
    setStatus("加载失败");
  }
}

main();
