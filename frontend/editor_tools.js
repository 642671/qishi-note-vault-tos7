(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.QishiEditorTools = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  function clampSelection(text, start, end) {
    var value = String(text == null ? "" : text);
    var safeStart = Math.max(0, Math.min(Number(start) || 0, value.length));
    var safeEnd = Math.max(safeStart, Math.min(Number(end) || safeStart, value.length));
    return { text: value, start: safeStart, end: safeEnd };
  }

  function wrapSelection(text, start, end, before, after, placeholder) {
    var safe = clampSelection(text, start, end);
    var selected = safe.text.slice(safe.start, safe.end);
    var body = selected || String(placeholder || "");
    var prefix = String(before || "");
    var suffix = String(after || "");
    return {
      text: safe.text.slice(0, safe.start) + prefix + body + suffix + safe.text.slice(safe.end),
      start: safe.start + prefix.length,
      end: safe.start + prefix.length + body.length
    };
  }

  function prefixLines(text, start, end, prefix) {
    var safe = clampSelection(text, start, end);
    var lineStart = safe.text.lastIndexOf("\n", Math.max(0, safe.start - 1)) + 1;
    var following = safe.text.indexOf("\n", safe.end);
    var lineEnd = following === -1 ? safe.text.length : following;
    var block = safe.text.slice(lineStart, lineEnd);
    var value = String(prefix || "");
    var lines = block.split("\n").map(function (line) {
      return line.length ? value + line : value.trimEnd();
    });
    var replaced = lines.join("\n");
    return {
      text: safe.text.slice(0, lineStart) + replaced + safe.text.slice(lineEnd),
      start: lineStart,
      end: lineStart + replaced.length
    };
  }

  function formatLink(text, start, end) {
    return wrapSelection(text, start, end, "[", "](https://)", "链接文字");
  }

  function documentStats(text) {
    var value = String(text == null ? "" : text);
    var cjkMatches = value.match(/[\u3400-\u9fff]/g) || [];
    var withoutCjk = value.replace(/[\u3400-\u9fff]/g, " ");
    var latinMatches = withoutCjk.match(/[A-Za-z0-9]+(?:['’_-][A-Za-z0-9]+)*/g) || [];
    return {
      characters: Array.from(value).length,
      lines: value.length ? value.split(/\r\n?|\n/).length : 0,
      words: cjkMatches.length + latinMatches.length
    };
  }

  return {
    clampSelection: clampSelection,
    wrapSelection: wrapSelection,
    prefixLines: prefixLines,
    formatLink: formatLink,
    documentStats: documentStats
  };
});
