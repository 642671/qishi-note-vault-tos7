"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const tools = require("../frontend/editor_tools.js");

test("wrapSelection keeps selected text and returns a usable selection", () => {
  const result = tools.wrapSelection("hello world", 6, 11, "**", "**", "text");
  assert.equal(result.text, "hello **world**");
  assert.equal(result.text.slice(result.start, result.end), "world");
});

test("wrapSelection inserts and selects a placeholder when nothing is selected", () => {
  const result = tools.wrapSelection("hello ", 6, 6, "[", "](https://)", "链接文字");
  assert.equal(result.text, "hello [链接文字](https://)");
  assert.equal(result.text.slice(result.start, result.end), "链接文字");
});

test("prefixLines formats every selected line without swallowing the next line", () => {
  const result = tools.prefixLines("one\ntwo\nthree", 0, 7, "- ");
  assert.equal(result.text, "- one\n- two\nthree");
  assert.equal(result.text.slice(result.start, result.end), "- one\n- two");
});

test("documentStats counts CJK characters, latin words, characters, and lines", () => {
  assert.deepEqual(tools.documentStats("你好 world\ncodex-测试"), {
    characters: 17,
    lines: 2,
    words: 6
  });
  assert.deepEqual(tools.documentStats(""), { characters: 0, lines: 0, words: 0 });
});
