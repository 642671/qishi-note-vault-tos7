# Changelog

## 1.0.015 - 2026-10-17

- 修正一个会误导用户的误报：前端把"请求根本没被浏览器或平台转发"也报成
  "后端服务未运行"。`Failed to fetch` 是网络层拒绝（证书不受信、连接被重置、
  CORS/CSP、页面处于沙箱来源），它不能证明后端进程没在跑。
- 实机（10.18.15.136，TOS 7.0.1298）测得：未登录时平台对任意 `/v2/...` 代理路径
  回的是 **HTTP 200** + `{"is_login":false,"code":false,"msg":"please login","code_num":117}`，
  不是 401。旧代码只看 `response.ok` 与 `data.ok`，会把这种回包当成"本应用健康检查通过"，
  随后显示 `Python undefined`。现在按 `is_login`/`code_num`/`msg` 识别平台信封并归为会话问题。
- 失败被分成四类，横幅按类给标题、正文、颜色和处置：`network`（请求没能离开浏览器）、
  `session`（TOS 会话没有生效）、`gateway`（平台代理没有转到本应用）、
  `offline`（后端服务返回了错误）。非 `offline` 用琥珀色，避免用户去重启一个本来就好的服务。
  横幅第一条永远是实际失败的那个请求地址。
- 代理根不再硬编码：按 `/v2/proxy/qishi-note-vault`、`/v2/proxy/qishi-note-vault/api`、
  `<页面所在前缀>/v2/proxy/qishi-note-vault` 三个候选顺序探测，判据是回包里的 `app` 字段
  等于本应用（规范 8.9 只承认第一种，规范 8.7 要求后端同时兼容后几种，实机固件也确实
  存在只在带前缀路径上放行代理的情况）。选定后复用；某天突然 404 时重新探测一次，不循环。
- 请求头收紧：无体 GET 不再带 `Content-Type`（它是 CORS 非安全字段，会让请求白付一次预检）；
  不再手写 `Cookie` 请求头——规范 8.10 要求带它，但浏览器把 `Cookie` 列为 forbidden header
  name，手写那条会被静默丢弃，而 `TMSESSNAME` 本身是 `HttpOnly`（实机 `Set-Cookie` 已验证），
  JS 读出来永远是空串。真实会话由 `credentials:"include"` 携带。
- 后端 CORS 修正：只在请求确实带 `Origin` 时回显跨源头并附 `Vary: Origin`，
  不再无条件回 `Access-Control-Allow-Origin: null`。作为对照，平台自身的 TOSDaemon
  回的是字面量 `Access-Control-Allow-Origin: same-origin`——那不是合法取值。
  启思笔记的二进制响应（附件、备份导出）同样带上跨源头。
- 400 与 401 语义分清：后端回 400（输入不合法）不再触发故障横幅，只把错误写进结果区；
  `serviceDown` 仅在真正可能是服务侧问题时为真。
- `network` 分型给出可自查的步骤：先看 F12 网络面板里这条请求是不是 `Blocked by client`
  （扩展拦截），再确认设备证书已被信任、页面确实是从应用中心打开的。实机上这一类最常见的原因
  是浏览器扩展或证书，而不是后端。
- 实机（TOS 7.0.1298）验证：未登录时平台对任意 `/v2/...` 路径回登录信封，`/v2/` 之外的路径回
  nginx 404 HTML，写错前缀（`/v2/proxy2/...`）被 301 静默改写；套接字侧 `/health`、`/api/health`、
  `/<appid>/health`、`/v2/proxy/<appid>/health` 四种路径全部命中；备份导出这类二进制响应带回了
  回显的 `Origin` 与 `Vary: Origin`；浏览器里四类分型、健康态、"重试连接"恢复，以及新建、读取、
  修改、列表、彻底删除这条完整链路都已逐条确认。
- 新增 `backend/tests/frontend_request_harness.cjs` 与
  `backend/tests/test_frontend_request_layer.py`：用 Node 的 vm 把 `frontend/app.js`
  连同假 `document`/`location`/`fetch` 真正跑起来，逐场景断言失败归类、代理根探测与复用、
  请求头形状，以及"非 JSON 但状态正常的响应必须原样交回"。字符串断言证明不了行为，
  这一版要的就是行为。另在路由测试中补 `CorsHeaderTests` 锁死预检与回显规则。
- 14 种语言的 `release_note` 更新至本版本。

## 1.0.014 - 2026-10-17

- 解决“安装成功但服务从未启动”这类最难自查的问题：`postinst` 不再只执行一次
  `systemctl start … || true` 就宣布安装完成，而是先校验启动前置条件
  （unit 文件、`bin/qishi-note-vault-service` 启动器、`/usr/bin/python3`），再轮询
  `systemctl is-active` 与 `/var/api/qishi-note-vault.sock` 是否真正就绪（最长 25 秒）。
- 前置条件缺失或 25 秒内未就绪时，`postinst` 生成
  `/usr/local/qishi-note-vault/logs/postinst-diagnostic.txt`：包含应用用户身份、启动前置条件清单、
  `systemctl is-active/status/cat` 输出、最近 120 行 `journalctl` 与 socket 状态，
  同时在安装日志输出 `ERROR` 级消息和下一步命令。脚本仍以 0 退出，避免把包留在
  “半配置”状态而无法卸载或升级。
- `postinst` 在启动前执行 `systemctl reset-failed`：上一版本崩溃循环耗尽 `StartLimitBurst`
  之后，systemd 会拒绝后续所有启动请求，现象正是“重装也起不来”。
- `webui.bz2` 解压失败不再中断 `configure`（此前 `set -e` 会把包停在半配置状态，
  并把真正原因——损坏的压缩包——藏在 dpkg 报错之后），改为记录 `ERROR` 并提示
  WebUI 可能仍是旧版本；每次 `configure` 仍重新解压，避免升级后继续服务旧前端。
- `prerm` 由“`systemctl stop/disable … || true`”改为可验证停用：停止后轮询确认进程真的
  退出（最长 14 秒），仍存活则升级 `systemctl kill` 发送 SIGKILL，再 `disable`。
  解决 App Center 显示已停用、但守护进程与 socket 仍然存在（Codex 记录的未完成项）。
- `prerm`/`postrm` 的 socket 清理加失败保护：`/var/api` 实机是 `1777` sticky 目录，
  别人属主的 socket 无法 unlink，未保护的 `rm -f` 会在 `set -e` 下中断脚本，
  使其后的 `disable`、`daemon-reload`、`userdel` 永不执行。
- `prerm` 的 shebang 由 `#!/bin/bash` 改为 `#!/bin/sh`，与 dpkg 实际调用的 shell 一致；
  三个生命周期脚本统一复核为 POSIX sh，不使用 `install -d`（TOS 自带的
  `/usr/bin/install` 会拒绝该用法）、`realpath`、`cp -a`。
- 前端新增“后端服务未运行”恢复指引：健康检查失败时展示具体失败原因、应用中心
  停用/重新启用步骤、本应用真实的 `systemctl status qishinotevault-system.service` 命令与诊断文件
  绝对路径，并提供“重试连接”按钮；重新连通后自动收起。指引条不占用顶部 56px
  标题栏，也不覆盖右下角 20×20 窗口缩放安全区。
- 新增 `backend/tests/test_lifecycle_scripts.py`，把上述实机结论固化为回归断言：
  POSIX sh 与禁用 bash 语法、禁用 TOS 上不可用的命令、启动前置条件检查、
  `reset-failed` 必须先于 `start`、就绪轮询同时看 unit 与 socket、诊断文件路径与页面文案
  一致、停用必须确认并升级、socket 清理必须可失败、`/var/api` 只创建不收紧。
- 14 种语言的 `release_note` 更新至本版本。

## 1.0.013 - 2026-10-17

- systemd 单元按官方 v2.7 规范与 TOS 7 实机行为重新校准：`TimeoutStopSec` 5 → 10（规范 8.12 必需值），
  `Restart` 由 `no` 改为 `on-failure` + `RestartSec=10`（规范 8.7.1：App Center 遵循开发者重启策略，
  崩溃可自愈；正常停用退出码为 0，不会被重新拉起）。
- 继续省略 `Group=<appid>` 与 `PrivateTmp=true`，并在 unit 内写明实机证据：
  TOS 7 只创建应用用户（主组 `allusers`）、不创建同名组，声明 `Group=` 会 `status=216/GROUP`；
  `/var/api` 是指向 `/tmp/api` 的符号链接，`PrivateTmp` 会 `status=226/NAMESPACE`。
- `postinst` 不再修改共享目录 `/var/api` 的属主和权限（实机为 `1777` sticky，应用用户本就能建自己的
  socket；规范 10.6 与 16.2 禁止应用改动共享权限，收窄成 `0770` 还会危及其他 iframe 应用与平台自身 socket），
  仅在该目录真正缺失时按 `1777` 创建。
- 实机注入残留 socket 后发现并修复两处启动/停用健壮性问题：`_remove_stale_socket()` 改用
  `os.path.lexists()`（悬空符号链接会被 `os.path.exists()` 判为不存在，随后 bind 以 `EADDRINUSE` 失败），
  并且当 sticky 目录不允许 unlink 时输出一条可执行的 `ERROR` 日志后以状态 1 退出，不再抛 `PermissionError`
  回溯；unit 的 `ExecStopPost` 加 `-` 前缀，避免清理失败把一次正常停用记成
  `Control process exited, status=1/FAILURE`（App Center 停用失败）。
- 新增 `backend/tests/test_socket_lifecycle.py` 覆盖以上 socket 生命周期行为。
- 新增 `backend/tests/test_package_contract.py`：把 v2.7 规范条目和上述实机结论固化为回归断言
  （config.ini 必填与互斥、版本五处同步、14 语言、图标 viewBox/大小、LF 与无 BOM、unit 指令、
  postinst/prerm/postrm 行为）。
- 14 种语言的 `release_note` 更新至本版本。



## 1.0.012 - 2026-10-08

- 保存成功提示改为页面顶部居中的绿色通知，错误仍使用红色。
- 新增浅色/暗色主题切换，并记住用户选择。
- 明确标题、正文、标签、搜索、附件、备份和分页上限，增加边界值测试。

## 1.0.011 - 2026-10-08

- 增加安装时应用用户缺失的兼容补建路径，修复新机器安装后 systemd 报 `217/USER`、启用失败的问题。

## 1.0.010 - 2026-10-08

- 应用内容边界铺满 TOS 窗口，仅保留 56px 宿主标题栏。
- 右下角增加 20×20 白色缩放安全角，避免窗口缩放热区遮挡编辑器内容。

## 1.0.009 - 2026-10-08

- 修复 TOS 升级时继续使用首次解压的旧 WebUI，确保标题栏和边框修改真正生效。
- `postinst` 现在每次安装或升级都会重新解压当前版本的 `webui.bz2`。

## 1.0.008 - 2026-10-08

- 按 TOS 7.0 上架复审意见移除 systemd `Group=` 指令，仅保留应用 `User=`，由 systemd 使用该用户的实际主组。
- `postinst` 改为动态读取应用用户主组，避免硬编码平台组名。

## 1.0.007 - 2026-10-08

- 兼容 TOS 平台代理转发到后端的 `/<app_id>/...`、`/<app_id>/api/...` 和嵌套代理路径，修复页面显示“后端服务不可用”。

## 1.0.006 - 2026-10-08

- 修复 TOS 共享 `/var/api` 目录为 `root:root 770` 时应用用户无法创建 iframe socket、后端以 `status=1` 退出的问题。

## 1.0.005 - 2026-10-08

- 移除与 TOS `/var/api -> /tmp/api` 共享 socket 路径冲突的 `PrivateTmp`，修复 systemd 启动时的 `226/NAMESPACE`。

## 1.0.004 - 2026-10-08

- 修复 TOS 应用用户没有同名组时 systemd 返回 `216/GROUP`，导致应用启用失败的问题。
- 修复应用数据目录仍为 root 所有、服务用户无法写入的问题。
- 不再修改多个 iframe 应用共享的 `/var/api` 所有权。

## 1.0.003 - 2026-10-08

- 增加与 TOS 宿主窗口一致的白色标题栏，进一步提高顶部安全区，避免内容进入关闭、最小化和最大化按钮区域。
- 停用服务时不再自动拉起，并将退出等待限制为 5 秒，确保 App Center 停用动作可以稳定完成。

## 1.0.002 - 2026-10-08

- 修复 WebUI 顶部关闭按钮、右侧滚动条和右下角缩放热区与页面内容重叠的问题。
- 修复应用中心停用失败问题，为服务停止增加有界等待和残留进程清理。

## 1.0.001 - 2026-10-07

- 首个 Qishi Note Vault 版本。
- 增加 Markdown 笔记创建、编辑、搜索和预览。
- 增加标签、置顶、归档和回收站。
- 增加图片、PDF、文本、Markdown 和压缩包附件。
- 增加 ZIP 完整备份和恢复。
- 使用 Python 3.10 标准库、SQLite 和 Unix Socket。
- 增加 x86_64/aarch64 构建脚本和 GitHub Actions 发布流程。
