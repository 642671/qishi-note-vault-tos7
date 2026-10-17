/*
 * 把浏览器脚本真正跑起来的行为测试，不是字符串断言。
 *
 * frontend/app.js 是纯浏览器脚本，它假定有 document、location、fetch。这里用
 * Node 的 vm 给它一个最小假环境，再按场景替换 fetch 的返回值，检查请求层到底把
 * 失败归到哪一类。之所以要跑到这个程度：实机（TOS 7.0.1298）上"后端挂了"和
 * "浏览器把请求丢了"在用户眼里一模一样，只有真实执行才能证明代码没有把它们混为
 * 一谈。用法：
 *
 *   node frontend_request_harness.cjs <app.js> <appid> [--binary]
 *
 * --binary 给启思笔记用：它的 apiRequest 在响应不是 JSON 但状态正常时必须把
 * Response 原样交回去（附件预览和备份导出走的就是这条路）。
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const appPath = path.resolve(process.argv[2]);
const APP_ID = process.argv[3];
const BINARY = process.argv.indexOf("--binary") >= 0;
const source = fs.readFileSync(appPath, "utf8");

function makeElement(id) {
  const attributes = Object.create(null);
  return {
    id: id,
    textContent: "",
    className: "",
    hidden: true,
    value: "",
    dataset: {},
    firstChild: null,
    children: [],
    appendChild: function (node) {
      this.children.push(node);
    },
    removeChild: function () {
      this.children.pop();
    },
    setAttribute: function (name, value) {
      attributes[name] = String(value);
    },
    getAttribute: function (name) {
      return name in attributes ? attributes[name] : null;
    },
    addEventListener: function () {},
    classList: {
      add: function () {},
      remove: function () {},
      toggle: function () {},
      contains: function () {
        return false;
      }
    },
    style: {},
    focus: function () {},
    click: function () {}
  };
}

let elements = new Map();

const documentStub = {
  cookie: "",
  readyState: "loading",
  documentElement: makeElement("html"),
  body: makeElement("body"),
  hidden: false,
  getElementById: function (id) {
    if (!elements.has(id)) {
      elements.set(id, makeElement(id));
    }
    return elements.get(id);
  },
  querySelector: function () {
    return null;
  },
  querySelectorAll: function () {
    return [];
  },
  createElement: function (tag) {
    return makeElement(tag);
  },
  createObjectURL: function () {
    return "blob:stub";
  },
  revokeObjectURL: function () {},
  addEventListener: function () {}
};

/*
 * 被测代码只用 response.headers.get(...) / response.status / response.ok /
 * text() / json()，所以这里做一个足够真的对象，不必引入 undici。
 */
function makeResponse(status, body, contentType) {
  const ok = status >= 200 && status < 300;
  const isJson = String(contentType).indexOf("application/json") >= 0;
  return {
    ok: ok,
    status: status,
    headers: {
      get: function (name) {
        return String(name).toLowerCase() === "content-type" ? contentType : null;
      }
    },
    text: async function () {
      return body;
    },
    json: async function () {
      if (!isJson) {
        throw new SyntaxError("not json");
      }
      return JSON.parse(body);
    },
    arrayBuffer: async function () {
      return new ArrayBuffer(body.length);
    }
  };
}

function health(extra) {
  return JSON.stringify(
    Object.assign({ status: "ok", app: APP_ID, version: "harness", python: "3.11.9" }, extra || {})
  );
}

const loginEnvelope = JSON.stringify({
  is_login: false,
  code: false,
  msg: "please login",
  data: null,
  code_num: 117,
  code_msg: "please login"
});

const sandbox = {
  document: documentStub,
  location: {
    pathname: "/" + APP_ID + "/index.html",
    href: "http://nas.example:8181/" + APP_ID + "/index.html",
    origin: "http://nas.example:8181",
    search: "",
    hash: ""
  },
  console: { log: function () {}, info: function () {}, warn: function () {}, error: function () {} },
  fetch: async function () {
    throw new Error("harness: no scenario installed");
  },
  setTimeout: setTimeout,
  clearTimeout: clearTimeout,
  setInterval: setInterval,
  clearInterval: clearInterval,
  localStorage: {
    getItem: function () {
      return null;
    },
    setItem: function () {},
    removeItem: function () {}
  },
  URL: URL,
  URLSearchParams: URLSearchParams,
  Blob: function Blob() {},
  FormData: function FormData() {},
  Intl: Intl,
  encodeURIComponent: encodeURIComponent,
  decodeURIComponent: decodeURIComponent
};
sandbox.window = sandbox;
sandbox.self = sandbox;

vm.createContext(sandbox);
vm.runInContext(source, sandbox, { filename: appPath });

let calls = [];

function reset() {
  elements = new Map();
  calls = [];
  documentStub.cookie = "";
  sandbox.apiRootReady = false;
  sandbox.API_ROOT = sandbox.API_CANDIDATES[0];
}

/*
 * handler(url, options, index) 返回 makeResponse(...)，抛异常就模拟网络层失败。
 * 第二个返回值用 capture 收集每次调用的 method/headers，验证请求头形状。
 */
function install(handler) {
  sandbox.fetch = async function (url, options) {
    const record = {
      url: url,
      method: (options && options.method) || "GET",
      credentials: options && options.credentials,
      headers: Object.assign({}, (options && options.headers) || {}),
      hasBody: options && options.body !== undefined && options.body !== null
    };
    calls.push(record);
    return handler(url, record, calls.length);
  };
}

async function classify(promiseFactory) {
  try {
    const value = await promiseFactory();
    return { threw: false, value: value };
  } catch (error) {
    return {
      threw: true,
      kind: error && error.kind ? error.kind : null,
      url: error && error.url ? error.url : null,
      detail: error && error.detail ? error.detail : null,
      serviceDown: !!(error && error.serviceDown),
      message: error && error.message ? error.message : String(error)
    };
  }
}

const businessPath = BINARY ? "/tags" : "calculate";
const businessBody = BINARY ? undefined : { operation: "date" };

async function businessCall() {
  return sandbox.apiRequest(businessPath, businessBody === undefined ? {} : businessBody);
}

const scenarios = [];

function scenario(name, runner) {
  scenarios.push([name, runner]);
}

scenario("platform-login-envelope-is-session", async function () {
  reset();
  install(function () {
    return makeResponse(200, loginEnvelope, "application/json; charset=utf-8");
  });
  return classify(sandbox.refreshApiRoot);
});

scenario("fetch-rejection-is-network", async function () {
  reset();
  install(function () {
    throw new TypeError("Failed to fetch");
  });
  return classify(sandbox.refreshApiRoot);
});

scenario("nginx-html-is-gateway", async function () {
  reset();
  install(function () {
    return makeResponse(404, "<html><body>404 Not Found</body></html>", "text/html");
  });
  return classify(sandbox.refreshApiRoot);
});

scenario("wrong-app-field-is-gateway", async function () {
  reset();
  install(function () {
    return makeResponse(200, health({ app: "some-other-app" }), "application/json; charset=utf-8");
  });
  return classify(sandbox.refreshApiRoot);
});

scenario("http-401-is-session", async function () {
  reset();
  install(function () {
    return makeResponse(401, JSON.stringify({ ok: false, error: "unauthorized" }), "application/json; charset=utf-8");
  });
  return classify(sandbox.refreshApiRoot);
});

scenario("second-candidate-wins-and-is-reused", async function () {
  reset();
  install(function (url) {
    if (url.indexOf("/api") >= 0) {
      return makeResponse(200, health(), "application/json; charset=utf-8");
    }
    return makeResponse(404, "<html>404</html>", "text/html");
  });
  const probe = await classify(sandbox.refreshApiRoot);
  const rootAfter = sandbox.API_ROOT;
  const readyAfter = sandbox.apiRootReady;
  const probeCalls = calls.length;
  const next = await classify(businessCall);
  return {
    probe: probe,
    business: next,
    rootAfter: rootAfter,
    readyAfter: readyAfter,
    expectedRoot: sandbox.API_CANDIDATES[1],
    candidates: sandbox.API_CANDIDATES,
    probeCalls: probeCalls,
    extraCalls: calls.length - probeCalls
  };
});

scenario("backend-500-is-offline", async function () {
  reset();
  install(function (url, record) {
    if (record.url.indexOf("health") >= 0) {
      return makeResponse(200, health(), "application/json; charset=utf-8");
    }
    return makeResponse(500, JSON.stringify({ ok: false, error: "internal server error" }), "application/json; charset=utf-8");
  });
  await classify(sandbox.refreshApiRoot);
  return classify(businessCall);
});

scenario("backend-400-is-rejected-and-not-service-down", async function () {
  reset();
  install(function (url) {
    if (url.indexOf("health") >= 0) {
      return makeResponse(200, health(), "application/json; charset=utf-8");
    }
    return makeResponse(400, JSON.stringify({ ok: false, error: "height must be a number" }), "application/json; charset=utf-8");
  });
  await classify(sandbox.refreshApiRoot);
  return classify(businessCall);
});

scenario("header-shape", async function () {
  reset();
  install(function (url) {
    if (url.indexOf("health") >= 0) {
      return makeResponse(200, health(), "application/json; charset=utf-8");
    }
    return makeResponse(200, JSON.stringify({ ok: true, result: {}, notes: [] }), "application/json; charset=utf-8");
  });
  await sandbox.refreshApiRoot();
  await businessCall();
  const requests = calls.map(function (call) {
    return {
      method: call.method,
      credentials: call.credentials,
      headerNames: Object.keys(call.headers).map(function (name) {
        return name.toLowerCase();
      }),
      hasBody: call.hasBody
    };
  });
  return { threw: false, value: { requests: requests } };
});

scenario("csrf-cookie-forwarded-and-session-not-fabricated", async function () {
  reset();
  documentStub.cookie = "TMSESSNAME=session-value; X-Csrf-Token=csrf-value";
  install(function (url) {
    if (url.indexOf("health") >= 0) {
      return makeResponse(200, health(), "application/json; charset=utf-8");
    }
    return makeResponse(200, JSON.stringify({ ok: true, result: {}, notes: [] }), "application/json; charset=utf-8");
  });
  await sandbox.refreshApiRoot();
  await businessCall();
  return {
    threw: false,
    value: {
      headers: calls.map(function (call) {
        return call.headers;
      })
    }
  };
});

scenario("gateway-error-triggers-one-reprobe", async function () {
  reset();
  let first = true;
  install(function () {
    if (first) {
      first = false;
      return makeResponse(200, health(), "application/json; charset=utf-8");
    }
    return makeResponse(502, "<html>502</html>", "text/html");
  });
  await classify(sandbox.refreshApiRoot);
  const callsBefore = calls.length;
  const result = await classify(businessCall);
  return {
    result: result,
    retried: calls.length - callsBefore,
    readyAfter: sandbox.apiRootReady
  };
});

if (BINARY) {
  scenario("non-json-ok-returns-response", async function () {
    reset();
    install(function (url) {
      if (url.indexOf("health") >= 0) {
        return makeResponse(200, health(), "application/json; charset=utf-8");
      }
      return makeResponse(200, "%PDF-1.4 stub bytes", "application/pdf");
    });
    await classify(sandbox.refreshApiRoot);
    try {
      const value = await sandbox.apiRequest("/attachments/7");
      return { threw: false, value: { status: value.status, hasArrayBuffer: typeof value.arrayBuffer === "function" } };
    } catch (error) {
      return { threw: true, kind: error.kind || null, message: error.message };
    }
  });
}

(async function main() {
  const results = [];
  for (const [name, runner] of scenarios) {
    try {
      results.push({ scenario: name, result: await runner() });
    } catch (error) {
      results.push({ scenario: name, harnessError: String(error && error.stack ? error.stack : error) });
    }
  }
  process.stdout.write(JSON.stringify({ appId: APP_ID, binary: BINARY, results: results }, null, 1));
})();
