# TOS 7 规范符合性说明（qishi-note-vault）

依据：`github.com/terramaster-tos/tos-app-pkg-tools`，`TOS 7 Application Development Guide`
doc v2.7，本地同步至提交 `9dc7e43d`（2026-09-30）。下文所有条目编号均为该指南章节号。

本文件同时记录在真实 TOS 7 设备上测得的行为。指南与平台实际行为不一致的地方，
以实机为准，并在第 4 节逐条说明偏离理由。

## 1. 验证环境

```text
设备        TerraMaster TNAS（TOS 7，Ubuntu 22.04.3 基底）
内核        6.12.63+ SMP x86_64
systemd     249 (249.11-0ubuntu3.11)
Python      3.10.12（/usr/bin/python3，符合指南 8.11 的系统 Python 假设）
共享 socket /var/api -> /tmp/api，属主 uid 0，模式 drwxrwxrwt（1777 sticky）
```

## 2. systemd 沙箱指令实测矩阵

在同一台机器上用一次性 `naslab-*` 单元逐个指令实测（每步都检查
`systemctl show` 的 `Result/ExecMainStatus` 与 `/var/api/<id>.sock` 是否响应 `GET /health`）：

| 配置 | 结果 |
|---|---|
| 全量加固指令 + `User=<非 root>` | `active/running`，socket 正常响应 |
| 单条 `NoNewPrivileges` / `PrivateDevices` / `ProtectKernelTunables` / `ProtectKernelModules` / `RestrictRealtime` / `RestrictSUIDSGID` / `LockPersonality` / `MemoryDenyWriteExecute` / `RestrictAddressFamilies` / `ProtectSystem` / `ProtectHome` / `ProtectControlGroups` | 全部单独通过 |
| 追加 `Group=<appid>`（平台未建同名组） | `status=216/GROUP`，`Failed to determine group credentials` |
| 追加 `PrivateTmp=true` | `status=226/NAMESPACE`，`Failed to set up mount namespacing: /run/systemd/unit-root/tmp/api` |
| 在 `/var/api/<appid>.sock` 预留一个别人属主的普通文件后启动 | 清理与 bind 都失败（sticky 目录只允许属主或 root 删除），服务以状态 1 退出 |

结论：本项目的 unit 保留指南 8.12 要求的全部加固指令，且这些指令在 TOS 7 上可用。
另外，`/tmp/api` 实测属主 uid 0、模式 `1777` 带 sticky 位，应用用户只能删除自己拥有的条目，
这一条决定了后端与 `ExecStopPost` 的写法（见第 3、4 节）。

## 3. 逐条符合性

| 规范条目 | 要求 | 本项目状态 |
|---|---|---|
| 8.4 config.ini | 扩展名 `.ini`、内容严格 JSON、无注释无尾逗号 | 符合，`json.loads` 可解析 |
| 8.4.2 必填字段 | id/icon/publisher/exec/version/recommend/beta/low_version/category/platform/application_type/system_id/package/user | 全部存在 |
| 8.4.1 子型互斥 | iframe 用 `type:"iframe"` + `path:"/<appid>/"`，禁止 `open_path` | 符合 |
| 8.4.2 recommend | 提交时必须为 `false` | 符合 |
| 8.4.2 id 规则 | 小写字母开头、`a-z0-9-`、≤50、发布后不可改 | 符合 |
| 8.5 app.lang | 14 个语言 section 齐全，`name`/`auth`/`descript` 非空 | 符合，且 14 种语言都写 `release_note` |
| 8.5.5 / 4.6 编码 | UTF-8 无 BOM，全部 LF 换行 | 符合，由回归测试守住 |
| 8.6 图标 | SVG、含 viewBox、≤50 KB、路径与 `config.ini.icon` 一致 | 符合 |
| 8.12 unit 必需指令 | `User`/`WorkingDirectory`/`NoNewPrivileges`/`ProtectSystem=strict`/`ProtectHome`/`TimeoutStartSec=30`/`TimeoutStopSec=10`/`ReadWritePaths` | 符合 |
| 8.12 / 12.9 socket | 监听 `/var/api/<appid>.sock`、启动前清理残留、`0660` | 符合（`backend/qishi_note_vault/server.py::_remove_stale_socket()` 用 `lexists` 覆盖悬空链接，清理失败时输出一条可读的 `ERROR` 并以状态 1 退出；`ExecStopPost=-/bin/rm -f` 让清理失败不会把正常停用记成失败） |
| 8.11 后端 | 自动创建 `/var/api`、`GET /health`、优雅处理 `SIGTERM`/`SIGINT` | 符合 |
| 8.7.1 崩溃处理 | 开发者可自定义 `Restart=`，App Center 按其执行 | `Restart=on-failure` + `RestartSec=10`，并用 `StartLimitBurst=5`/`IntervalSec=60` 抑制崩溃循环 |
| 8.13 control | `Package`/`Version`/`Architecture`/`Maintainer`/`Depends` | 符合，`Depends: python3 (>= 3.10), systemd` |
| 8.15 / 16.1 校验和 | 随包提供 SHA-256 | `build.py` 生成 `.deb.sha256` |
| 10.7 资源配额 | `Utilities` 类目默认 `CPUQuota=50%`、`MemoryMax=256M`，且不得写死 `/Volume*` | 符合，配额只作为可上调的默认值 |
| 16.2 安全 | 非 root 运行、不改系统敏感目录 | 符合，见第 4 节第 3 条 |
| 16.2 合规 | 仓库公开、README 完整、描述与功能一致 | 符合 |

## 4. 三处刻意偏离（都有实机证据）

1. **不写 `Group=<appid>`。** 指南 8.12 把 `Group` 列为必需，但 TOS 7 创建应用用户时主组是
   `allusers`，并不创建同名组；声明后 systemd 在 `ExecStart` 之前就以 `216/GROUP` 失败，
   应用永远无法启用。强行改成别的组还会丢掉 `allusers` 成员身份，而 10.6 的共享文件夹
   读权限正依赖该成员身份。因此 unit 省略该指令，让 systemd 使用用户真实主组。

2. **不写 `PrivateTmp=true`。** 指南 12.9.5 把 `PrivateTmp` 列为可选项，但本平台 `/var/api`
   实际是指向 `/tmp/api` 的符号链接；启用私有 `/tmp` 后挂载命名空间会因
   `/run/systemd/unit-root/tmp/api` 不存在而报 `226/NAMESPACE`，服务无法启动。

3. **`postinst` 不修改 `/var/api` 的属主和权限。** 该目录由平台拥有、模式 `1777` sticky，
   任何应用用户本就能在其中创建自己的 socket。指南 10.6 明确"应用不得直接修改共享权限"，
   16.2 的安全审查同样禁止越界写系统目录；把共享目录收窄为 `0770 root:<某应用主组>`
   还会危及其他 iframe 应用与平台自身（`Application.sock` 等）的套接字。
   因此脚本只在 `/var/api` 确实缺失时按平台自身的 `1777` 创建它。

另外说明一点：`postinst` 保留了"应用用户不存在时补建"的兼容分支。指南 8.12 假设平台已建好
用户并要求脚本不要 `useradd`，但侧载安装（App Center 手动安装 / `dpkg -i`）不保证建用户，
缺失时服务会以 `217/USER` 失败。该分支只在 `getent passwd <appid>` 查不到用户时才动作，
不覆盖平台已创建的用户，也不使用 `root`。

## 5. 回归护栏

`backend/tests/test_package_contract.py` 把上面每一条目变成断言：config.ini 必填与互斥、
五处版本号同步（`VERSION`、`config.ini`、`DEBIAN/control`、后端 `__version__`、前端 `?v=`）、
14 语言字段非空、图标限制、LF 与无 BOM、unit 必需指令与两处刻意偏离、`postinst`/`prerm`/`postrm`
行为。规范或实机结论若发生变化，测试会先失败，而不是等到装到 NAS 上才发现。

`backend/tests/test_lifecycle_scripts.py` 是第 6 节那类故障的护栏，逐条检查三个生命周期脚本：
POSIX sh 且不含 bash 专有语法、不使用 TOS 上不可用的命令、启动前置条件检查、
`reset-failed` 必须早于 `start`、就绪轮询同时看 unit 与 socket、诊断文件路径与页面文案一致、
停用必须确认后再清理、socket 清理必须可失败、`/var/api` 只创建不收紧。

```bash
py -3 -m unittest discover -s backend/tests -v
```

Windows 上的 `python` / `python3` 可能是 Microsoft Store 的转发存根（退出码 49、无输出），
本项目统一用 `py -3`；在 NAS 或普通 Linux 上直接写 `python3` 即可。

## 6. “安装成功但服务从未启动”这一类故障

指南只要求 `postinst` 启动服务，没有要求证明它真的在服务。实机上最难自查的正是这一点：
dpkg 返回 0、App Center 显示已启用，页面却什么都没有。以下每一条都在
`10.18.15.136`（TOS 7 x86_64）上复现并验证过。

| 症状 | 根因 | 现在的处理 |
|---|---|---|
| 安装“成功”，服务从未监听 | `systemctl start … \|\| true` 吞掉一切失败 | 启动前校验 unit、启动器、`/usr/bin/python3`；启动后轮询 `is-active` 与 `/var/api/<appid>.sock`（最长 25 秒） |
| 失败后无从排查 | 安装日志只有一行 shell 输出 | 生成 `<APP_DIR>/logs/postinst-diagnostic.txt`：身份、前置条件清单、`systemctl is-active/status/cat`、最近 120 行 `journalctl`、socket 状态，并在日志输出 `ERROR` 与下一步命令 |
| 修好也起不来 | 崩溃循环耗尽 `StartLimitBurst` 后，systemd 在 60 秒窗口内拒绝一切新启动请求 | `start` 之前执行 `systemctl reset-failed` |
| 页面白屏且无提示 | 前端只把健康检查失败记成一个小圆点 | 后端不可用时显示恢复指引条：失败原因、应用中心停用/重新启用、本应用真实的 `systemctl status` 命令、诊断文件绝对路径，并提供“重试连接” |
| App Center 显示已停用，进程与 socket 仍在 | `prerm` 用 `\|\| true` 假设停止成功 | 停止后轮询确认（最长 14 秒），仍存活则 `systemctl kill` 升级，再 `disable` |
| 卸载/清出中途失败、残留单元文件 | `/var/api` 是 `1777` sticky，别人属主的 socket 无法 unlink，未保护的 `rm -f` 在 `set -e` 下中断脚本 | socket 清理一律允许失败并给出 `WARN` |
| `webui.bz2` 损坏时包卡在半配置状态 | `set -e` 下解压失败直接退出 | 解压失败改为记录 `ERROR` 并提示 WebUI 可能是旧版本；每次 `configure` 仍重新解压 |
| 生命周期脚本用了 TOS 上不存在的行为 | `/usr/bin/install` 是定制版，拒绝 `install -d` | 三个脚本只用 `mkdir -p` + `chmod`，并由测试禁止 `install -`、`realpath`、`cp -a` |

`postinst` 在失败路径上仍然以 0 退出，这是刻意的：非零退出会把包留在“半配置”状态，
用户既不能升级也不能卸载，只能手工 `dpkg --configure -a`。失败信息改由日志级别、
诊断文件和页面提示条三层传递。

`prerm` 的 SIGKILL 升级路径在正常配置下是二重保险：unit 的 `TimeoutStopSec=10` 早于
`prerm` 的 14 秒轮询，systemd 自己会先完成强杀。实测用一个忽略 `SIGTERM` 的进程验证：
`dpkg -r` 用时 12 秒，进程、socket、单元文件全部清除，`/var/api` 属主与模式保持
`777 test:test` 未被改动。

## 7. 请求层：四类失败与代理路由实测（1.0.015）

触发这一轮改动的是页面上这句提示：

```text
后端服务未运行 …… 失败原因：无法连接后端服务：Failed to fetch
```

`Failed to fetch` 是 fetch 在网络层抛出的 `TypeError`，只说明请求没能离开浏览器或没拿到响应，
**不能**证明后端进程没在运行。旧代码把所有异常都归为"后端没运行"，所以这句话在多数情况下是错的。
下面先列在 `10.18.15.136`（TOS 7.0.1298）上实测到的平台行为，再说明据此重写的请求层。
启思笔记与"生活工具箱"共用同一套设计，本节措辞按本应用改写。

### 7.1 代理链路的实测结论

| 位置 | 实测行为 |
|---|---|
| nginx | `location ~ ^/v2/(.*)` 去掉 `/v2/` 后 `proxy_pass http://127.0.0.1:5050/$1?$args`（TOSDaemon） |
| 未登录时任意 `/v2/` 路径 | **HTTP 200** + `{"is_login":false,"code":false,"msg":"please login","data":null,"code_num":117,"code_msg":"please login"}`，包括 `/v2/proxy/一个不存在的appid/health` |
| 不在 `/v2/` 下的路径 | nginx 404 + `text/html`（`<title>404 Not Found</title>`） |
| `/v2/` 下不需要登录的未知接口 | TOSDaemon 404 + `text/plain`，正文 `404 page not found` |
| 写错前缀，如 `/v2/proxy2/qishi-note-vault/health` | **301** 重写到 `/proxy2/qishi-note-vault/health/?`，然后才 404 |
| 80 / 443 端口 | 只 `return 301` 到 8181 / 5443；两个端口的 `root` 都是 `/usr/www/`，include 同一份 extend 配置 |
| 平台自己的 OPTIONS 预检 | 204，但 `Access-Control-Allow-Origin: same-origin` —— 非法字面值，浏览器不会接受 |
| CSP | `default-src 'self' *.terra-master.com …`，没有 `connect-src`，同源 fetch 放行 |
| 会话 cookie | `TMSESSNAME` 是 HttpOnly（JS 读不到），`X-Csrf-Token` 不是 |

鉴权发生在路由之前，这一条决定了 `gateway` 与 `session` 的先后：未登录时无论路径写什么，
`/v2/proxy/...` 都先被登录信封挡下，只有会话有效才会去查注册表。301 那一行则说明状态码根本
不能当判据——一个拼错的前缀不报错，只是安静地跳走。

由此确定的判据是：**看回包内容，不看 HTTP 状态码。** 200 可能是登录信封，404 可能是 nginx 的
HTML 或 TOSDaemon 的纯文本，301 可能是拼错的前缀，它们都不是本应用；`/health` 回包必须带
`app == "qishi-note-vault"` 才算命中路由。

浏览器与套接字两侧的验收（1.0.015，`10.18.15.136`）：

- 套接字侧直连 `/var/api/qishi-note-vault.sock`：`/health`、`/api/health`、
  `/qishi-note-vault/health`、`/v2/proxy/qishi-note-vault/health` 四种路径全部回同一个健康 JSON。
- 套接字侧的请求头：不带 `Origin` 的同源请求没有任何 `Access-Control-*` 头；带 `Origin` 时回显该值
  并附 `Vary: Origin`；`OPTIONS` 预检回 204 且 `Access-Control-Allow-Headers` 覆盖 `Content-Type`、
  `X-Csrf-Token`、`Cookie`；`/backup/export` 这类二进制响应同样带回显头，且 `Content-Type:
  application/zip` 与 `Content-Disposition: attachment` 正常。
- 平台侧（页面放在 `/usr/www/probe-qn/`，与 TOS 同源）：未登录时页面显示"TOS 会话没有生效"
  而不是"后端服务未运行"——这正是旧版本误报的那一格；把候选根改成 `/nope/health` 得到
  "平台代理没有转到本应用"，改成指向不存在的端口得到"无法连接平台代理 ……
  详情：TypeError: Failed to fetch"。
- 正常回包与后端故障改用一个把 `/v2/proxy/<appid>/<api>` 转给 unix socket 的本地桩
  （`.nas-lab/serve_webui.py`）取证：徽标 `service-badge is-online`、控制台打印
  `Qishi Note Vault 1.0.015 Python 3.10.12 via /v2/proxy/qishi-note-vault`、提示条 `hidden`，
  并且新建、读取、修改、列表、彻底删除这条链路全部走通（验证数据当场删除）；把桩换成一律回 502
  的形态后提示条转为红色 `offline` 并列出 `systemctl status` 与诊断文件；修好桩再点"重试连接"，
  徽标恢复、提示条收起。
- 需要说清楚的边界：**"会话有效时平台代理会把请求转到本应用"这一条只在套接字侧直连验证过。**
  TOS 网页登录是 RSA 包装的，脚本无法登录，因此没有带有效会话在平台代理上跑通过一次真实请求。
  `offline` 分型也相应地是用桩复现的，而不是在平台代理上观测到的。

### 7.2 四类失败分型

`apiFailure()` 给每个错误打上 `kind`，提示条按 `kind` 渲染各自的标题、正文、配色和处置建议：

| kind | 触发条件 | 标题 | 提示的下一步 |
|---|---|---|---|
| `network` | fetch 抛异常，含 `Failed to fetch` | 无法连接平台代理 | 先显示实际失败的请求地址，再按 F12 网络面板区分：`Blocked by client` 是扩展拦的，证书不受信与沙箱来源也在这一类 |
| `session` | 401，或回包命中登录信封 | TOS 会话没有生效 | 重新登录 TOS、确认页面是从应用中心打开的 |
| `gateway` | 回包不是本应用 JSON（nginx HTML、`app` 字段不符、JSON 解析失败） | 平台代理没有转到本应用 | 重新探测候选路由，并把三种形式列给用户核对 |
| `offline` | 后端 5xx | 后端服务未运行 | `systemctl status qishinotevault-system.service` 与诊断文件绝对路径 |
| `rejected` | 后端 4xx（401 除外） | 不弹提示条 | 只在结果区/toast 显示错误，徽标保持"在线" |

`error.serviceDown` 只在非 `rejected` 时为真，所以一次填错的请求（后端回 400）不再拉故障横幅。
`network` / `session` / `gateway` 三种用 `--notice-accent: var(--accent)` 显示为提醒色而不是红色，
只有真正指向后端的 `offline` 才是红色；徽标对应 `.service-badge.is-warn`。提示条本身加了
`max-height: min(56vh, 420px)` 与 `overflow-y: auto`，因为步骤列表里现在会列出不止一个 URL，
不能让它顶出窗口或压住右下 20×20 的缩放角。

### 7.3 路由探测

规范 8.9 把 `/v2/proxy/<appid>/<api>` 定为唯一形式，8.7 又要求后端兼容
`/v2/proxy/<appid>/api/<resource>` 与 `<config.ini.path>/<api_name>`，个别固件确实只在带前缀的路径上
放行代理。前端按序探测三个候选——`/v2/proxy/qishi-note-vault`、`/v2/proxy/qishi-note-vault/api`、
`<页面目录>/v2/proxy/qishi-note-vault`——选中第一个回包 `app` 字段匹配的根并复用。探测只在根未就绪
或出现 `gateway` 错误时发生，`session` 与 `network` 立即上抛，不做无谓重试。

### 7.4 非 JSON 响应仍然交给调用方

附件预览与备份导出（`/backup/export`）走的是正常 200 但 `Content-Type` 不是 JSON 的路。
`send()` 在这种情况下直接返回原始 `Response`，由调用方读 `blob()`/`FormData` 边界，
只有 401 或非 `ok` 才升级为 `session` / `gateway`。这条分支由 harness 的
`non-json-ok-returns-response` 场景和 `CorsHeaderTests::test_binary_response_also_carries_echoed_origin` 守住。

### 7.5 后端 CORS 修正

旧实现取不到 `Origin` 时兜底回 `Access-Control-Allow-Origin: null`，这种头对任何请求都没有意义。
现在 `_apply_cors()` 只在请求确实带 `Origin` 时回显该值，并同时附
`Access-Control-Allow-Credentials: true` 与 `Vary: Origin`；同源请求不带 `Origin`，因此不再收到 CORS 头。
`_send_bytes()`（附件与备份）与 `do_OPTIONS` 走同一套逻辑，预检回 204 并放行平台那组请求头。
平台自己在预检里回 `Access-Control-Allow-Origin: same-origin` 是非法字面值——这个错误不复制一遍。

### 7.6 请求头收紧

GET 不带 `Content-Type`：它是 CORS 非安全字段，会让每个只读请求先付一次预检。
规范 8.10 要求同时带 `X-Csrf-Token` 和 `Cookie`，但 `Cookie` 在浏览器的 forbidden header name 名单里，
fetch 里手写的那条被静默丢弃（既不报错也不发送），而真正的 `TMSESSNAME` 本身是 HttpOnly，
JS 读出来永远是空串。会话实际由 `credentials: "include"` 携带，所以只保留真正能生效的 `X-Csrf-Token`。
`platformHeaders(isJson)` 保留这个参数是因为图片上传走 `FormData`：那里的 `Content-Type` 必须留给浏览器
生成，手写会丢掉 multipart boundary。

### 7.7 护栏

`backend/tests/frontend_request_harness.cjs` 用 Node 的 `vm` 起一个带假
`document` / `location` / `fetch` 的沙箱执行真实的 `app.js`，逐场景断言分型结果（11 个共享场景，
本应用因二进制响应多 1 个，共 12 个）。
`backend/tests/test_frontend_request_layer.py` 跑这个 harness，再补充源码与样式连线断言：提示条的
五个 id 与 `data-kind="offline"` 默认值、三种提醒色的 CSS 规则与 `--notice-accent`、
"实际失败的请求地址"必须引用 `error.url`、禁止出现手写 `Cookie` 头与 `getCookie("TMSESSNAME")`、
三个前端文件保持 LF 无 BOM。`CorsHeaderTests` 对真实 socket 发裸 HTTP 请求校验头，
含"回显 `Origin` 而不是硬编码"和二进制响应同样带头。

必须说清楚的局限：用户设备上的这一次 `Failed to fetch` 未能在 `10.18.15.136` 复现——同一请求形式在
该机可通——而且实验机侧载安装后没有 App Center 注册（`/usr/www/qishi-note-vault` 缺失），页面本身无法
由平台下发，所以原始客户端原因只能靠新版本横幅第一条"实际失败的请求地址"回到用户那边取证。
按实机证据，这一类现象的可能原因从高到低是：浏览器扩展拦截（F12 显示 `Blocked by client`）、
证书不受信或走了 TDM 远程 / 反向代理、会话没生效（新版会归为 `session` 而不是这一类）、
应用未在应用中心注册（归 `gateway`）、后端真的挂了（归 `offline`）。
已经排除的假设：8181 与 5443 两个 server 块的 `root` 同为 `/usr/www/` 并 include 同一份 extend
配置，"页面与接口不同端口"不成立；CSP 没有 `connect-src`；GET 不带自定义头不触发预检。
本轮可交付的价值是：应用现在能指明四类原因中的哪一类并显示实际失败的 URL，
同时修掉三个真实缺陷（把 200 登录信封当健康、没有路由回退、措辞误导）。
