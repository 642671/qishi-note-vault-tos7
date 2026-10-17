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

```bash
py -3 -m unittest discover -s backend/tests -v
```

Windows 上的 `python` / `python3` 可能是 Microsoft Store 的转发存根（退出码 49、无输出），
本项目统一用 `py -3`；在 NAS 或普通 Linux 上直接写 `python3` 即可。
