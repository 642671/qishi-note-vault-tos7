# Qishi Note Vault for TOS 7

`qishi-note-vault` 是一个运行在 TOS 7 上的私有 Markdown 知识库，当前版本为 `1.0.001`。

应用使用 WebUI 内部打开模式，前端通过 TOS 平台代理访问本地 Python 服务：

```text
浏览器
  -> /v2/proxy/qishi-note-vault/...
  -> /var/api/qishi-note-vault.sock
  -> Python 3.10 后端
  -> SQLite + attachments/
```

笔记和附件全部保存在 NAS 应用数据目录，不依赖外部数据库或云服务。

## 功能

- Markdown 笔记创建、编辑和保存
- 三栏知识库界面
- 标题和正文搜索
- 标签筛选
- 置顶、归档和回收站
- Markdown 预览
- 图片、PDF、文本、Markdown 和压缩包附件
- ZIP 完整备份和恢复
- 14 种 TOS 语言
- Python 3.10 标准库后端，无第三方 Python 依赖

## 目录结构

```text
qishi-note-vault-tos7/
├── .github/workflows/build.yml
├── backend/
│   ├── qishi-note-vault-service
│   ├── qishi_note_vault/
│   │   ├── __init__.py
│   │   ├── database.py
│   │   ├── notes.py
│   │   └── server.py
│   └── tests/
├── frontend/
│   ├── index.html
│   ├── app.js
│   └── styles.css
├── images/icons/qishi-note-vault.svg
├── init.d/qishinotevault-system.service
├── DEBIAN/
│   ├── control
│   ├── postinst
│   ├── prerm
│   └── postrm
├── docs/
│   ├── API.md
│   ├── BUILD_AND_RELEASE_GUIDE_CN.md
│   └── PROJECT_LAYOUT.md
├── config.ini
├── qishi-note-vault.lang
├── qishi-note-vault.env
├── build.py
├── VERSION
└── CHANGELOG.md
```

## Python 后端

TOS 7 预装 Python 3.10，因此应用直接使用 `/usr/bin/python3`，不需要 Node.js、Java、PostgreSQL 或 Redis。

后端分为四层：

- `database.py`：SQLite 表结构、笔记、标签、附件和导入导出数据。
- `notes.py`：业务服务、附件文件、ZIP 备份和恢复。
- `server.py`：HTTP-over-Unix-Socket API、请求校验和信号处理。
- `qishi-note-vault-service`：安装包中的启动入口。

运行测试：

```bash
python -m unittest discover -s backend/tests -v
```

Windows PowerShell 没有可用的 `python` 命令时，使用：

```powershell
py -3 -m unittest discover -s backend/tests -v
```

## 构建

完整的版本修改、测试、打包、GitHub 发布和 TOS 安装步骤见 [测试、打包与发布详细指南](docs/BUILD_AND_RELEASE_GUIDE_CN.md)。

构建脚本只使用 Python 标准库，不要求本机安装 `dpkg-deb`。

```bash
python build.py --platform x86_64
python build.py --platform aarch64
```

生成文件：

```text
dist/qishi-note-vault_1.0.001_x86_64.deb
dist/qishi-note-vault_1.0.001_x86_64.deb.sha256
```

构建脚本会自动检查：

- `config.ini` 是否为合法 JSON 且字段符合单包 iframe 规范。
- 语言文件是否包含 TOS 要求的 14 个 section。
- 图标是否为安全 SVG 且不超过 50 KB。
- `control`、数据包结构和可执行权限。
- `webui.bz2` 是否为正确的 bzip2 归档。

## 数据与备份

应用运行数据位于：

```text
/usr/local/qishi-note-vault/data/
├── notes.db
└── attachments/
```

界面中的“导出”会生成包含 `notes.json` 和附件的 ZIP 文件。迁移或恢复时，使用“导入”选择该 ZIP 文件。

## 发布

每次发布需要同时更新：

1. `VERSION`
2. `config.ini` 的 `version`
3. `DEBIAN/control` 的 `Version`
4. `CHANGELOG.md`

发布标签格式：

```bash
git tag v1.0.001
git push origin main --tags
```

GitHub Actions 会构建 `x86_64` 和 `aarch64` 安装包，并附加 SHA256 文件到 Release。

仓库地址：`https://github.com/642671/qishi-note-vault-tos7`
