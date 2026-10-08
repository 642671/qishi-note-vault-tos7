"use strict";

var APP_ID = "qishi-note-vault";
var API_ROOT = "/v2/proxy/" + APP_ID;
var MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024;
var MAX_BACKUP_BYTES = 64 * 1024 * 1024;
var state = {
  notes: [],
  tags: [],
  current: null,
  selectedId: "",
  view: "active",
  tag: "",
  query: "",
  dirty: false,
  saveTimer: 0,
  previewMode: "split",
  theme: "light"
};

function byId(id) {
  return document.getElementById(id);
}

function getCookie(name) {
  var prefix = encodeURIComponent(name) + "=";
  return document.cookie.split(";").map(function (item) {
    return item.trim();
  }).find(function (item) {
    return item.indexOf(prefix) === 0;
  })?.slice(prefix.length) || "";
}

function platformHeaders(isJson) {
  var session = getCookie("TMSESSNAME");
  var csrf = getCookie("X-Csrf-Token");
  var headers = {
    "X-Csrf-Token": csrf,
    "Cookie": "TMSESSNAME=" + session + "; X-Csrf-Token=" + csrf + ";"
  };
  if (isJson) {
    headers["Content-Type"] = "application/json";
  }
  return headers;
}

async function apiRequest(path, options) {
  var requestOptions = options || {};
  var response = await fetch(API_ROOT + path, {
    method: requestOptions.method || "GET",
    credentials: "include",
    headers: platformHeaders(requestOptions.json === true),
    body: requestOptions.body
  });
  var contentType = response.headers.get("Content-Type") || "";
  if (contentType.indexOf("application/json") < 0) {
    return response;
  }
  var data = await response.json();
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || "请求失败");
  }
  return data;
}

function toast(message, tone) {
  var element = byId("toast");
  element.className = "toast toast-" + (tone || "success");
  element.textContent = message;
  element.classList.add("is-visible");
  window.clearTimeout(element._timer);
  element._timer = window.setTimeout(function () {
    element.classList.remove("is-visible");
  }, 2600);
}

function toastError(error) {
  toast(error && error.message ? error.message : String(error), "error");
}

function applyTheme(theme) {
  state.theme = theme === "dark" ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", state.theme);
  var icon = byId("themeIcon");
  var button = byId("themeToggle");
  var dark = state.theme === "dark";
  if (icon) {
    icon.setAttribute("href", dark ? "#i-sun" : "#i-moon");
  }
  if (button) {
    var label = dark ? "切换到浅色模式" : "切换到暗色模式";
    button.title = label;
    button.setAttribute("aria-label", label);
  }
  try {
    localStorage.setItem(APP_ID + "-theme", state.theme);
  } catch (error) {
    // Storage can be unavailable in privacy-restricted browser contexts.
  }
}

function initializeTheme() {
  var saved = "";
  try {
    saved = localStorage.getItem(APP_ID + "-theme") || "";
  } catch (error) {
    saved = "";
  }
  var preferred = saved || (window.matchMedia
    && window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light");
  applyTheme(preferred);
}

function toggleTheme() {
  applyTheme(state.theme === "dark" ? "light" : "dark");
}

function setServiceState(online) {
  byId("serviceBadge").className = "service-badge " + (online ? "is-online" : "is-offline");
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, function (character) {
    return {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      "\"": "&quot;",
      "'": "&#39;"
    }[character];
  });
}

function safeUrl(url) {
  return /^(https?:\/\/|\/|#)/i.test(url) ? url : "";
}

function renderInline(source) {
  var text = escapeHtml(source);
  var codeTokens = [];
  text = text.replace(/`([^`]+)`/g, function (_, code) {
    codeTokens.push("<code>" + code + "</code>");
    return "\u0000CODE" + (codeTokens.length - 1) + "\u0000";
  });
  text = text.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, function (_, alt, url) {
    var safe = safeUrl(url);
    return safe ? '<img src="' + safe + '" alt="' + alt + '">' : alt;
  });
  text = text.replace(/\[([^\]]+)\]\(([^)]+)\)/g, function (_, label, url) {
    var safe = safeUrl(url);
    return safe ? '<a href="' + safe + '" target="_blank" rel="noreferrer">' + label + "</a>" : label;
  });
  text = text.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  text = text.replace(/__([^_]+)__/g, "<strong>$1</strong>");
  text = text.replace(/\*([^*]+)\*/g, "<em>$1</em>");
  text = text.replace(/~~([^~]+)~~/g, "<del>$1</del>");
  return text.replace(/\u0000CODE(\d+)\u0000/g, function (_, index) {
    return codeTokens[Number(index)];
  });
}

function renderMarkdown(source) {
  var lines = String(source || "").replace(/\r\n?/g, "\n").split("\n");
  var html = [];
  var paragraph = [];
  var listType = "";
  var code = false;
  var codeLines = [];

  function flushParagraph() {
    if (paragraph.length) {
      html.push("<p>" + renderInline(paragraph.join(" ")) + "</p>");
      paragraph = [];
    }
  }

  function closeList() {
    if (listType) {
      html.push("</" + listType + ">");
      listType = "";
    }
  }

  lines.forEach(function (line) {
    if (line.indexOf("```") === 0) {
      flushParagraph();
      closeList();
      if (code) {
        html.push("<pre><code>" + escapeHtml(codeLines.join("\n")) + "</code></pre>");
        codeLines = [];
        code = false;
      } else {
        code = true;
      }
      return;
    }
    if (code) {
      codeLines.push(line);
      return;
    }
    if (!line.trim()) {
      flushParagraph();
      closeList();
      return;
    }
    var heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      closeList();
      var level = heading[1].length;
      html.push("<h" + level + ">" + renderInline(heading[2]) + "</h" + level + ">");
      return;
    }
    if (/^(---|\*\*\*)$/.test(line.trim())) {
      flushParagraph();
      closeList();
      html.push("<hr>");
      return;
    }
    var unordered = line.match(/^\s*[-*+]\s+(.+)$/);
    var ordered = line.match(/^\s*\d+\.\s+(.+)$/);
    if (unordered || ordered) {
      flushParagraph();
      var nextType = unordered ? "ul" : "ol";
      if (listType !== nextType) {
        closeList();
        listType = nextType;
        html.push("<" + listType + ">");
      }
      html.push("<li>" + renderInline((unordered || ordered)[1]) + "</li>");
      return;
    }
    if (line.indexOf("> ") === 0) {
      flushParagraph();
      closeList();
      html.push("<blockquote>" + renderInline(line.slice(2)) + "</blockquote>");
      return;
    }
    paragraph.push(line);
  });

  flushParagraph();
  closeList();
  if (code) {
    html.push("<pre><code>" + escapeHtml(codeLines.join("\n")) + "</code></pre>");
  }
  return html.join("");
}

function formatDate(timestamp) {
  if (!timestamp) {
    return "";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  }).format(new Date(timestamp * 1000));
}

function tagsFromInput() {
  var normalized = [];
  var seen = {};
  byId("noteTags").value.split(",").some(function (value) {
    var tag = value.trim().slice(0, 40);
    var key = tag.toLocaleLowerCase();
    if (tag && !seen[key]) {
      seen[key] = true;
      normalized.push(tag);
    }
    return normalized.length >= 20;
  });
  return normalized;
}

function updateCounts(stats) {
  var data = stats || {};
  byId("countActive").textContent = data.total || 0;
  byId("countPinned").textContent = data.pinned || 0;
  byId("countArchived").textContent = data.archived || 0;
  byId("countTrashed").textContent = data.trashed || 0;
}

function renderTags() {
  var container = byId("tagList");
  container.textContent = "";
  state.tags.forEach(function (item) {
    var button = document.createElement("button");
    button.type = "button";
    button.className = "tag-chip" + (state.tag === item.name ? " is-active" : "");
    button.textContent = item.name + " " + item.count;
    button.addEventListener("click", function () {
      state.tag = state.tag === item.name ? "" : item.name;
      loadNotes();
    });
    container.appendChild(button);
  });
}

function renderNotes() {
  var list = byId("noteList");
  var labels = {
    active: "全部笔记",
    pinned: "置顶笔记",
    archived: "归档笔记",
    trash: "回收站"
  };
  byId("listTitle").textContent = labels[state.view] || "全部笔记";
  byId("listSubtitle").textContent = state.notes.length + " 条记录";
  list.textContent = "";
  if (!state.notes.length) {
    var empty = document.createElement("div");
    empty.className = "empty-list";
    empty.textContent = "没有符合条件的笔记";
    list.appendChild(empty);
    return;
  }
  state.notes.forEach(function (note) {
    var button = document.createElement("button");
    button.type = "button";
    button.className = "note-item" + (note.id === state.selectedId ? " is-active" : "");

    var title = document.createElement("div");
    title.className = "note-item-title";
    if (note.pinned) {
      var pin = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      pin.innerHTML = '<use href="#i-pin"></use>';
      title.appendChild(pin);
    }
    title.appendChild(document.createTextNode(note.title || "Untitled"));

    var excerpt = document.createElement("div");
    excerpt.className = "note-item-excerpt";
    excerpt.textContent = (note.body || "空笔记").replace(/\s+/g, " ").slice(0, 140);

    var meta = document.createElement("div");
    meta.className = "note-item-meta";
    var tags = document.createElement("span");
    tags.className = "note-tag-mini";
    tags.textContent = note.tags.join(" · ");
    var date = document.createElement("span");
    date.textContent = formatDate(note.updated_at);
    meta.appendChild(tags);
    meta.appendChild(date);

    button.appendChild(title);
    button.appendChild(excerpt);
    button.appendChild(meta);
    button.addEventListener("click", function () {
      selectNote(note.id);
    });
    list.appendChild(button);
  });
}

function renderAttachments() {
  var container = byId("attachmentList");
  container.textContent = "";
  var attachments = state.current?.attachments || [];
  attachments.forEach(function (item) {
    var chip = document.createElement("div");
    chip.className = "attachment-chip";
    var link = document.createElement("a");
    link.href = API_ROOT + "/attachments/" + item.id;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = item.filename;
    var remove = document.createElement("button");
    remove.type = "button";
    remove.className = "attachment-remove";
    remove.title = "删除附件";
    remove.innerHTML = "&times;";
    remove.addEventListener("click", async function () {
      if (!window.confirm("确定删除附件 " + item.filename + " 吗？")) {
        return;
      }
      try {
        await apiRequest("/attachments/" + item.id, { method: "DELETE" });
        await selectNote(state.selectedId);
        toast("附件已删除");
      } catch (error) {
        toastError(error);
      }
    });
    chip.appendChild(link);
    chip.appendChild(remove);
    container.appendChild(chip);
  });
}

function updatePreview() {
  byId("markdownPreview").innerHTML = renderMarkdown(byId("noteBody").value);
}

function applyPreviewMode() {
  var grid = byId("editorGrid");
  grid.classList.toggle("preview-only", state.previewMode === "preview");
  grid.classList.toggle("editor-only", state.previewMode === "editor");
}

function markDirty() {
  state.dirty = true;
  byId("saveState").textContent = "未保存";
  byId("saveState").classList.add("is-dirty");
  updatePreview();
  window.clearTimeout(state.saveTimer);
  state.saveTimer = window.setTimeout(function () {
    saveCurrent(true);
  }, 1200);
}

async function loadTags() {
  var data = await apiRequest("/tags");
  state.tags = data.items || [];
  renderTags();
}

async function loadNotes() {
  var params = new URLSearchParams({
    view: state.view,
    query: state.query,
    tag: state.tag
  });
  var data = await apiRequest("/notes?" + params.toString());
  state.notes = data.items || [];
  updateCounts(data.stats);
  renderNotes();
}

async function selectNote(id) {
  if (state.dirty) {
    await saveCurrent(true);
  }
  var data = await apiRequest("/notes/" + encodeURIComponent(id));
  state.current = data.note;
  state.selectedId = id;
  byId("emptyState").classList.add("is-hidden");
  byId("editorWorkspace").classList.remove("is-hidden");
  byId("noteTitle").value = state.current.title || "";
  byId("noteBody").value = state.current.body || "";
  byId("noteTags").value = (state.current.tags || []).join(", ");
  byId("restoreButton").classList.toggle("is-hidden", !state.current.trashed);
  byId("trashButton").title = state.current.trashed ? "永久删除" : "移入回收站";
  byId("archiveButton").title = state.current.archived ? "取消归档" : "归档";
  byId("pinButton").title = state.current.pinned ? "取消置顶" : "置顶";
  state.dirty = false;
  byId("saveState").textContent = "已保存";
  byId("saveState").classList.remove("is-dirty");
  updatePreview();
  renderAttachments();
  renderNotes();
}

async function saveCurrent(silent) {
  if (!state.current || !state.selectedId) {
    return;
  }
  window.clearTimeout(state.saveTimer);
  var data = await apiRequest("/notes/" + encodeURIComponent(state.selectedId), {
    method: "PUT",
    json: true,
    body: JSON.stringify({
      title: byId("noteTitle").value,
      body: byId("noteBody").value,
      tags: tagsFromInput()
    })
  });
  state.current = Object.assign({}, state.current, data.note);
  state.dirty = false;
  byId("saveState").textContent = "已保存";
  byId("saveState").classList.remove("is-dirty");
  updateCounts((await apiRequest("/stats")).stats);
  await Promise.all([loadTags(), loadNotes()]);
  if (!silent) {
    toast("笔记已保存");
  }
}

async function createNote() {
  if (state.dirty) {
    await saveCurrent(true);
  }
  var data = await apiRequest("/notes", {
    method: "POST",
    json: true,
    body: JSON.stringify({
      title: "新笔记",
      body: "# 新笔记\n\n",
      tags: []
    })
  });
  state.view = "active";
  state.tag = "";
  document.querySelectorAll(".view-button").forEach(function (button) {
    button.classList.toggle("is-active", button.dataset.view === "active");
  });
  await Promise.all([loadTags(), loadNotes()]);
  await selectNote(data.note.id);
  byId("noteTitle").focus();
  byId("noteTitle").select();
}

async function setFlag(action, value) {
  if (!state.selectedId) {
    return;
  }
  var data = await apiRequest("/notes/" + encodeURIComponent(state.selectedId) + "/" + action, {
    method: "POST",
    json: true,
    body: JSON.stringify({ value: value })
  });
  state.current = Object.assign({}, state.current, data.note);
  await Promise.all([loadTags(), loadNotes()]);
  await selectNote(state.selectedId);
}

async function trashCurrent() {
  if (!state.selectedId) {
    return;
  }
  if (state.current.trashed) {
    if (!window.confirm("永久删除这条笔记及其附件？此操作无法恢复。")) {
      return;
    }
    await apiRequest("/notes/" + encodeURIComponent(state.selectedId) + "?permanent=true", {
      method: "DELETE"
    });
    state.current = null;
    state.selectedId = "";
    byId("editorWorkspace").classList.add("is-hidden");
    byId("emptyState").classList.remove("is-hidden");
    toast("笔记已永久删除");
  } else {
    await apiRequest("/notes/" + encodeURIComponent(state.selectedId), { method: "DELETE" });
    state.current = null;
    state.selectedId = "";
    byId("editorWorkspace").classList.add("is-hidden");
    byId("emptyState").classList.remove("is-hidden");
    toast("笔记已移入回收站");
  }
  await Promise.all([loadTags(), loadNotes()]);
}

async function restoreCurrent() {
  if (!state.selectedId) {
    return;
  }
  await apiRequest("/notes/" + encodeURIComponent(state.selectedId) + "/restore", {
    method: "POST",
    json: true,
    body: JSON.stringify({})
  });
  state.view = "active";
  document.querySelectorAll(".view-button").forEach(function (button) {
    button.classList.toggle("is-active", button.dataset.view === "active");
  });
  await Promise.all([loadTags(), loadNotes()]);
  await selectNote(state.selectedId);
  toast("笔记已恢复");
}

async function exportBackup() {
  var response = await fetch(API_ROOT + "/backup/export", {
    credentials: "include",
    headers: platformHeaders(false)
  });
  if (!response.ok) {
    throw new Error("导出失败");
  }
  var blob = await response.blob();
  var link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "qishi-note-vault-backup.zip";
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(link.href);
  toast("备份已导出");
}

async function importBackup(file) {
  if (file.size > MAX_BACKUP_BYTES) {
    throw new Error("备份文件不能超过 64 MB");
  }
  var form = new FormData();
  form.append("file", file, file.name);
  var response = await fetch(API_ROOT + "/backup/import", {
    method: "POST",
    credentials: "include",
    headers: platformHeaders(false),
    body: form
  });
  var data = await response.json();
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || "导入失败");
  }
  await Promise.all([loadTags(), loadNotes()]);
  toast("导入完成：" + data.imported + " 条新增，" + data.updated + " 条更新");
}

async function uploadAttachment(file) {
  if (!state.selectedId) {
    return;
  }
  if (!file.size) {
    throw new Error("附件不能为空");
  }
  if (file.size > MAX_ATTACHMENT_BYTES) {
    throw new Error("单个附件不能超过 8 MB");
  }
  var form = new FormData();
  form.append("note_id", state.selectedId);
  form.append("file", file, file.name);
  var response = await fetch(API_ROOT + "/attachments", {
    method: "POST",
    credentials: "include",
    headers: platformHeaders(false),
    body: form
  });
  var data = await response.json();
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || "附件上传失败");
  }
  var attachment = data.attachment;
  var url = API_ROOT + "/attachments/" + attachment.id;
  var image = /^image\/(png|jpeg|gif|webp)$/i.test(attachment.content_type);
  var prefix = byId("noteBody").value && !byId("noteBody").value.endsWith("\n") ? "\n\n" : "";
  byId("noteBody").value += prefix + (image
    ? "![" + attachment.filename + "](" + url + ")"
    : "[" + attachment.filename + "](" + url + ")") + "\n";
  markDirty();
  await selectNote(state.selectedId);
  toast("附件已添加");
}

function bindEvents() {
  byId("themeToggle").addEventListener("click", toggleTheme);
  byId("newNoteButton").addEventListener("click", function () {
    createNote().catch(toastError);
  });
  byId("saveButton").addEventListener("click", function () {
    saveCurrent(false).catch(toastError);
  });
  byId("previewButton").addEventListener("click", function () {
    state.previewMode = state.previewMode === "split"
      ? "preview"
      : state.previewMode === "preview"
        ? "editor"
        : "split";
    applyPreviewMode();
  });
  byId("pinButton").addEventListener("click", function () {
    setFlag("pin", !state.current.pinned).catch(toastError);
  });
  byId("archiveButton").addEventListener("click", function () {
    setFlag("archive", !state.current.archived).catch(toastError);
  });
  byId("trashButton").addEventListener("click", function () {
    trashCurrent().catch(toastError);
  });
  byId("restoreButton").addEventListener("click", function () {
    restoreCurrent().catch(toastError);
  });
  byId("exportButton").addEventListener("click", function () {
    exportBackup().catch(toastError);
  });
  byId("importButton").addEventListener("click", function () {
    byId("importFileInput").click();
  });
  byId("importFileInput").addEventListener("change", function (event) {
    var file = event.target.files[0];
    event.target.value = "";
    if (file) {
      importBackup(file).catch(toastError);
    }
  });
  byId("attachmentButton").addEventListener("click", function () {
    byId("attachmentFileInput").click();
  });
  byId("attachmentFileInput").addEventListener("change", function (event) {
    var file = event.target.files[0];
    event.target.value = "";
    if (file) {
      uploadAttachment(file).catch(toastError);
    }
  });
  document.querySelectorAll(".view-button").forEach(function (button) {
    button.addEventListener("click", function () {
      state.view = button.dataset.view;
      state.selectedId = "";
      document.querySelectorAll(".view-button").forEach(function (item) {
        item.classList.toggle("is-active", item === button);
      });
      byId("editorWorkspace").classList.add("is-hidden");
      byId("emptyState").classList.remove("is-hidden");
      loadNotes().catch(toastError);
    });
  });
  byId("searchInput").addEventListener("input", function (event) {
    state.query = event.target.value;
    window.clearTimeout(state._searchTimer);
    state._searchTimer = window.setTimeout(function () {
      loadNotes().catch(toastError);
    }, 250);
  });
  byId("noteTitle").addEventListener("input", markDirty);
  byId("noteBody").addEventListener("input", markDirty);
  byId("noteTags").addEventListener("input", markDirty);
  document.addEventListener("keydown", function (event) {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
      event.preventDefault();
      saveCurrent(false).catch(toastError);
    }
  });
}

async function initialize() {
  initializeTheme();
  bindEvents();
  try {
    var health = await apiRequest("/health");
    setServiceState(true);
    console.info("Qishi Note Vault", health.version, "Python", health.python);
    await Promise.all([loadTags(), loadNotes()]);
  } catch (error) {
    setServiceState(false);
    toast("后端服务不可用", "error");
  }
}

document.addEventListener("DOMContentLoaded", initialize);
