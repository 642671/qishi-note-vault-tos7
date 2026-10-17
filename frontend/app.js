"use strict";

var APP_ID = "qishi-note-vault";

/*
 * 平台代理地址。TOS 7 规范 8.9 把 /v2/proxy/<appid>/<api> 定为唯一形式，
 * 7.0.1298 实机验证它由 nginx 的 location ~ ^/v2/(.*) 去掉 /v2/ 后转给
 * 127.0.0.1:5050（TOSDaemon）。规范 8.7 又要求后端兼容
 * /v2/proxy/<appid>/api/<resource> 和 <config.ini.path>/<api_name>，个别固件
 * 确实只在带前缀的路径上放行代理。所以按顺序探测，选中第一个"回到本应用 JSON"
 * 的候选后复用。判据必须是回包内容而不是 HTTP 状态码：未登录时平台对任意 /v2/
 * 路径都回 200 + {"is_login":false,"code_num":117}，路径没命中时 nginx 回
 * 404 + HTML，两者都不是本应用。
 */
var API_CANDIDATES = (function buildCandidates() {
  var dir = location.pathname.slice(0, location.pathname.lastIndexOf("/") + 1);
  var list = [
    "/v2/proxy/" + APP_ID,
    "/v2/proxy/" + APP_ID + "/api",
    dir + "v2/proxy/" + APP_ID
  ];
  var unique = [];
  for (var i = 0; i < list.length; i += 1) {
    if (unique.indexOf(list[i]) < 0) {
      unique.push(list[i]);
    }
  }
  return unique;
})();

var API_ROOT = API_CANDIDATES[0];
var apiRootReady = false;
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

/*
 * 规范 8.10 要求前端同时带 X-Csrf-Token 和 Cookie 两个请求头，但浏览器把 Cookie
 * 列在 forbidden header name 名单里：fetch 里手写的那条会被静默丢弃（不报错、也不
 * 发送），而真正的 TMSESSNAME 本身是 HttpOnly，JS 读出来永远是空串。会话实际靠
 * credentials:"include" 自动携带，所以这里只保留真正能生效的 X-Csrf-Token。
 */
function platformHeaders(isJson) {
  var headers = {};
  var csrf = getCookie("X-Csrf-Token");
  if (csrf) {
    headers["X-Csrf-Token"] = csrf;
  }
  if (isJson) {
    headers["Content-Type"] = "application/json";
  }
  return headers;
}

var FAILURE_SUMMARY = {
  network: "请求没能离开浏览器",
  session: "TOS 会话没有生效",
  gateway: "平台代理没有转到本应用",
  offline: "后端服务返回了错误",
  rejected: "后端服务拒绝了这次请求"
};

function apiFailure(kind, url, detail) {
  var error = new Error(FAILURE_SUMMARY[kind] + "：" + detail);
  error.kind = kind;
  error.url = url;
  error.detail = detail;
  // 400 是这次输入不行，不是服务故障，不该弹故障横幅。
  error.serviceDown = kind !== "rejected";
  return error;
}

function isLoginEnvelope(data) {
  return !!data && (data.is_login === false || data.code_num === 117 || data.msg === "please login");
}

async function send(root, path, options) {
  var requestOptions = options || {};
  var url = root + path;
  var response;
  try {
    response = await fetch(url, {
      method: requestOptions.method || "GET",
      credentials: "include",
      headers: platformHeaders(requestOptions.json === true),
      body: requestOptions.body
    });
  } catch (error) {
    // TypeError: Failed to fetch 只说明请求在网络层失败（证书不受信、连接被重置、
    // CORS/CSP 拦截、页面处于沙箱来源），它并不能证明后端进程没在跑。
    throw apiFailure("network", url, (error.name || "Error") + ": " + error.message);
  }
  var contentType = response.headers.get("Content-Type") || "";
  if (contentType.indexOf("application/json") < 0) {
    if (response.status === 401) {
      throw apiFailure("session", url, "HTTP 401");
    }
    if (!response.ok) {
      throw apiFailure("gateway", url, "HTTP " + response.status + " 且响应不是 JSON");
    }
    // 附件与备份导出走的就是这条路：状态正常但不是 JSON，交给调用方处理。
    return response;
  }
  var data = await response.json().catch(function () {
    return null;
  });
  if (data === null) {
    throw apiFailure("gateway", url, "HTTP " + response.status + " 的响应体不是合法 JSON");
  }
  if (response.status === 401 || isLoginEnvelope(data)) {
    throw apiFailure("session", url, "平台回了未登录响应（HTTP " + response.status + "）");
  }
  if (!response.ok || data.ok === false) {
    var error = apiFailure(response.status >= 500 ? "offline" : "rejected", url,
      data.error || ("HTTP " + response.status));
    error.data = data;
    throw error;
  }
  return data;
}

async function refreshApiRoot() {
  var lastError = null;
  for (var i = 0; i < API_CANDIDATES.length; i += 1) {
    var root = API_CANDIDATES[i];
    var data;
    try {
      data = await send(root, "/health", {});
    } catch (error) {
      // 会话问题换路径也没用，立刻上报，别把三次失败都算成"后端没起来"。
      if (error.kind === "session" || error.kind === "network") {
        throw error;
      }
      lastError = error;
      continue;
    }
    if (data.app !== APP_ID) {
      lastError = apiFailure("gateway", root + "/health", "回包的 app 字段是 " + data.app);
      continue;
    }
    API_ROOT = root;
    apiRootReady = true;
    return data;
  }
  if (lastError) {
    throw lastError;
  }
  throw apiFailure("gateway", API_CANDIDATES[0] + "/health", "所有候选代理路径都不可用");
}

async function apiRequest(path, options) {
  if (!apiRootReady) {
    await refreshApiRoot();
  }
  try {
    return await send(API_ROOT, path, options);
  } catch (error) {
    // 已选定的路径突然不通（固件变更、代理规则改过），重新探测一次再重试。
    if (error.kind === "gateway") {
      apiRootReady = false;
      await refreshApiRoot();
      return await send(API_ROOT, path, options);
    }
    throw error;
  }
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
  // 任何一次调用发现服务不可用（不只是开屏那一次），都要把横幅按真实成因挂出来。
  if (error && error.serviceDown) {
    setServiceState(noticeKindOf(error) === "offline" ? "offline" : "warn");
    setServiceNotice(true, error);
  }
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

function setServiceState(state) {
  var badge = byId("serviceBadge");
  if (badge) {
    badge.className = "service-badge is-" + state;
  }
}

/*
 * 四种失败各有各的成因，不能统一写成"后端服务未运行"：浏览器在网络层丢掉请求
 * （Failed to fetch）、平台判定未登录、代理没命中本应用、后端真的回了错误，
 * 处置方式完全不同。横幅按 kind 切换标题、正文、颜色，并显示真正失败的那个 URL。
 */
var NOTICE_COPY = {
  network: {
    title: "无法连接平台代理",
    reason: "请求被浏览器拦在了网络层，没有走到启思笔记的后端，所以现在还不能断定服务本身没在运行。"
  },
  session: {
    title: "TOS 会话没有生效",
    reason: "平台拒绝把请求转发给启思笔记：当前页面没有拿到有效的 TOS 登录会话，后端服务本身可能是正常的。"
  },
  gateway: {
    title: "平台代理没有转到本应用",
    reason: "请求发出去了，但回到的是 nginx 或 TOS 平台的响应，不是启思笔记的接口。"
  },
  offline: {
    title: "后端服务未运行",
    reason: "启思笔记的本地服务返回了错误，笔记内容暂时无法读写。"
  }
};

var NOTICE_STEPS = {
  unit: { pre: "SSH 执行 ", code: "systemctl status qishinotevault-system.service", post: " 查看失败原因。" },
  diagnostics: {
    pre: "安装阶段的诊断记录在 ",
    code: "/usr/local/qishi-note-vault/logs/postinst-diagnostic.txt",
    post: "。"
  }
};

function noticeKindOf(error) {
  if (error && NOTICE_COPY[error.kind]) {
    return error.kind;
  }
  return "offline";
}

function noticeSteps(kind, error) {
  var steps = [{ pre: "实际失败的请求地址：", code: error.url }];
  if (kind === "session") {
    steps.push("先在同一个浏览器里登录 TOS 桌面，再从应用中心打开启思笔记。");
    steps.push({
      pre: "未登录时平台固定回 ",
      code: "{\"is_login\":false,\"code_num\":117}",
      post: "，那不是本应用的响应。"
    });
    steps.push("通过 TDM 远程或反向代理访问时换成局域网地址试试；证书不受信时浏览器同样会丢掉请求。");
    return steps;
  }
  if (kind === "network") {
    steps.push("按 F12 打开网络面板看这条请求：显示 (blocked) 或 Blocked by client 时是浏览器扩展拦的，" +
      "把这台设备加进例外再刷新。");
    steps.push("确认浏览器已经信任这台设备的证书（地址栏不再提示不安全），或改用局域网地址直接访问。");
    steps.push("确认页面是从 TOS 应用中心打开的，而不是本地另存的 HTML 副本——脱离设备域名时同源请求必然失败。");
    steps.push(NOTICE_STEPS.unit);
    return steps;
  }
  if (kind === "gateway") {
    steps.push("确认应用已经在应用中心完成注册，平台才知道该把请求转给哪个套接字。");
    steps.push(NOTICE_STEPS.unit);
    steps.push(NOTICE_STEPS.diagnostics);
    return steps;
  }
  steps.push("在应用中心先停用，再重新启用启思笔记。");
  steps.push(NOTICE_STEPS.unit);
  steps.push(NOTICE_STEPS.diagnostics);
  return steps;
}

function renderNoticeSteps(steps) {
  var list = byId("serviceNoticeSteps");
  if (!list) {
    return;
  }
  while (list.firstChild) {
    list.removeChild(list.firstChild);
  }
  steps.forEach(function (step) {
    var item = document.createElement("li");
    if (typeof step === "string") {
      item.textContent = step;
    } else {
      item.textContent = step.pre || "";
      var code = document.createElement("code");
      code.textContent = step.code;
      item.appendChild(code);
      if (step.post) {
        item.appendChild(document.createTextNode(step.post));
      }
    }
    list.appendChild(item);
  });
}

function setServiceNotice(visible, error) {
  var notice = byId("serviceNotice");
  var title = byId("serviceNoticeTitle");
  var text = byId("serviceNoticeReason");
  if (!notice || !title || !text) {
    return;
  }
  if (!visible) {
    notice.hidden = true;
    return;
  }
  var kind = noticeKindOf(error);
  var copy = NOTICE_COPY[kind];
  notice.setAttribute("data-kind", kind);
  title.textContent = copy.title;
  text.textContent = copy.reason + " 详情：" + (error.detail || error.message);
  renderNoticeSteps(noticeSteps(kind, error));
  notice.hidden = false;
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
  await connectBackend();
  byId("serviceNoticeRetry").addEventListener("click", function () {
    setServiceNotice(false);
    connectBackend().catch(toastError);
  });
}

// 一次健康探测同时驱动状态点和故障横幅：装好却从未对外服务的进程会自己解释自己，
// 而不是留下一个空白界面。这里刻意用 refreshApiRoot()，重试时重新探测所有候选路径。
async function connectBackend() {
  try {
    var health = await refreshApiRoot();
    setServiceState("online");
    setServiceNotice(false);
    console.info("Qishi Note Vault", health.version, "Python", health.python, "via", API_ROOT);
    await Promise.all([loadTags(), loadNotes()]);
  } catch (error) {
    var kind = noticeKindOf(error);
    setServiceState(kind === "offline" ? "offline" : "warn");
    setServiceNotice(true, error);
    toast(FAILURE_SUMMARY[kind] || "后端服务不可用", "error");
  }
}

document.addEventListener("DOMContentLoaded", initialize);
