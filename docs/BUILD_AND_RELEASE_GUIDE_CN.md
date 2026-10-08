# 启思笔记：测试、打包与发布详细指南

本文档适用于 `qishi-note-vault` 项目，目标是让你自己完成：

1. 修改版本号。
2. 运行测试。
3. 在 Windows 本地生成 `.deb` 安装包。
4. 检查安装包内容和 SHA256。
5. 提交并推送到 GitHub。
6. 通过 GitHub Actions 自动构建和创建 Release。
7. 在 TOS/TNAS 上安装和验证。
8. 处理常见错误。

## 1. 项目位置

Windows 本地仓库：

```text
D:\111\dev-test\qishi-note-vault-tos7
```

GitHub 仓库：

```text
https://github.com/642671/qishi-note-vault-tos7
```

应用信息：

```text
应用 ID：qishi-note-vault
应用名称：启思笔记 / Qishi Note Vault
包类型：Deb 单包
打开方式：WebUI 内部 iframe
后端：Python 3.10 / SQLite / Unix Socket
当前版本：1.0.007
systemd 服务：qishinotevault-system.service
```

## 2. 环境准备

### 2.1 Windows 打包所需环境

必须安装：

- Python 3.10 或更高版本
- Git

检查 Python：

```powershell
py -3 --version
```

检查 Git：

```powershell
git --version
```

如果 `py -3` 不可用，但 `python` 可用，也可以把文档中的：

```text
py -3
```

替换为：

```text
python
```

本项目打包只使用 Python 标准库，不要求 Windows 安装 `dpkg-deb`。

### 2.2 GitHub 发布所需环境

需要：

- 可访问 GitHub
- 对仓库 `642671/qishi-note-vault-tos7` 有写入权限
- Git 已配置用户名和邮箱

检查 Git 配置：

```powershell
git config --global user.name
git config --global user.email
```

如果未配置：

```powershell
git config --global user.name "qishi"
git config --global user.email "你的邮箱"
```

## 3. 进入项目目录

每次操作先进入仓库：

```powershell
cd D:\111\dev-test\qishi-note-vault-tos7
```

确认仓库状态：

```powershell
git status --short --branch
```

理想状态：

```text
## main...origin/main
```

如果显示已修改文件，先确认这些修改是否是你需要的，不要直接覆盖。

## 4. 版本号管理

### 4.1 当前版本

当前版本是：

```text
1.0.007
```

新版本必须使用数字和点，例如：

```text
1.0.002
1.0.003
1.1.000
2.0.000
```

TOS App Center 按数字段比较版本，所以不要使用：

```text
v1.0.002
1.0.2-beta
latest
```

### 4.2 修改版本时需要同步的文件

发布新版本时，需要同时修改：

1. `VERSION`
2. `config.ini`
3. `DEBIAN/control`
4. `CHANGELOG.md`

#### VERSION

```text
1.0.002
```

#### config.ini

```json
{
  "version": "1.0.002"
}
```

#### DEBIAN/control

```text
Version: 1.0.002
```

#### CHANGELOG.md

新增记录，例如：

```markdown
## 1.0.002 - 2026-10-08

- 修复笔记搜索问题。
- 增加新的 Markdown 快捷操作。
```

### 4.3 为什么必须同步

`build.py` 会检查：

- `VERSION`
- `config.ini` 的 `version`
- `DEBIAN/control` 的 `Version`

如果版本不一致，构建可能失败，或者安装包显示的版本与系统记录版本不同。

## 5. 运行测试

进入项目根目录后执行：

```powershell
py -3 -m unittest discover -s backend/tests -v
```

期望结果：

```text
Ran 7 tests

OK
```

测试内容包括：

- SQLite 笔记创建、更新、搜索和标签。
- 置顶、归档、回收站和恢复。
- 附件上传和下载。
- ZIP 备份和恢复。
- HTTP API 生命周期。

如果测试失败，先修复测试，不要继续打包。

## 6. 本地预览应用

启动开发服务器：

```powershell
py -3 dev_server.py --port 4173
```

浏览器打开：

```text
http://127.0.0.1:4173/
```

建议手动检查：

1. 新建笔记。
2. 输入标题、正文和标签。
3. 等待自动保存。
4. 检查 Markdown 预览。
5. 搜索笔记。
6. 置顶、归档、移入回收站和恢复。
7. 上传一个图片或 PDF 附件。
8. 导出 ZIP 备份。
9. 导入 ZIP 备份。
10. 调整浏览器窗口，检查移动端布局。

开发服务器使用：

```text
build\dev-data
```

作为测试数据目录。该目录不会提交到 Git。

## 7. 本地构建 Deb 安装包

### 7.1 构建 x86_64

```powershell
py -3 build.py --platform x86_64
```

### 7.2 构建 aarch64

```powershell
py -3 build.py --platform aarch64
```

构建成功示例：

```text
Built: D:\111\dev-test\qishi-note-vault-tos7\dist\qishi-note-vault_1.0.002_x86_64.deb
Verification: OK
SHA256: ...
Size: ... bytes
```

### 7.3 输出目录

```text
dist/
├── qishi-note-vault_1.0.002_x86_64.deb
├── qishi-note-vault_1.0.002_x86_64.deb.sha256
├── qishi-note-vault_1.0.002_aarch64.deb
└── qishi-note-vault_1.0.002_aarch64.deb.sha256
```

`dist` 和 `build` 已加入 `.gitignore`，不会提交到 Git。

不要手动修改：

```text
build/
dist/
```

它们都是构建脚本生成的临时文件和安装包。应修改源码后重新运行 `build.py`。

## 8. build.py 做了什么

运行 `build.py` 时会自动：

1. 读取 `VERSION`。
2. 读取并校验 `config.ini`。
3. 检查应用 ID、systemd ID、包名和路径。
4. 检查 14 种语言是否齐全。
5. 检查 SVG 图标大小和安全规则。
6. 将 Python 模块复制到 Debian staging 目录。
7. 将 `frontend/` 压缩为 `webui.bz2`。
8. 将前端、后端、配置、图标、systemd 服务放入数据包。
9. 生成 `control.tar.gz` 和 `data.tar.gz`。
10. 手工写入 Debian `ar` 容器，生成 `.deb`。
11. 重新读取 `.deb` 并验证版本、架构、路径和可执行权限。
12. 生成 SHA256 文件。

因此，Windows 没有 `dpkg-deb` 也可以生成 `.deb`。

## 9. 检查生成文件

### 9.1 查看文件

```powershell
Get-ChildItem .\dist
```

### 9.2 计算 SHA256

```powershell
Get-FileHash -Algorithm SHA256 .\dist\qishi-note-vault_1.0.002_x86_64.deb
```

结果应与：

```text
dist\qishi-note-vault_1.0.002_x86_64.deb.sha256
```

中的值一致。

### 9.3 在 Linux 上检查

如果本机有 Ubuntu、WSL 或 Linux 环境：

```bash
dpkg-deb --info qishi-note-vault_1.0.002_x86_64.deb
dpkg-deb --contents qishi-note-vault_1.0.002_x86_64.deb
```

应包含：

```text
/usr/local/qishi-note-vault/config.ini
/usr/local/qishi-note-vault/qishi-note-vault.lang
/usr/local/qishi-note-vault/images/icons/qishi-note-vault.svg
/usr/local/qishi-note-vault/bin/qishi-note-vault-service
/usr/local/qishi-note-vault/init.d/qishinotevault-system.service
/usr/local/qishi-note-vault/webui.bz2
```

## 10. 提交到 Git

先查看修改：

```powershell
git status
git diff
```

添加文件：

```powershell
git add .
```

再次确认：

```powershell
git status
```

提交：

```powershell
git commit -m "release: v1.0.002"
```

推送主分支：

```powershell
git push origin main
```

## 11. 通过 GitHub Actions 自动发布

### 11.1 创建版本标签

```powershell
git tag v1.0.002
```

### 11.2 推送标签

```powershell
git push origin v1.0.002
```

也可以一次推送主分支和标签：

```powershell
git push origin main --tags
```

### 11.3 GitHub Actions 自动执行

推送标签后，GitHub Actions 会：

1. 检出源码。
2. 安装 Python。
3. 运行 `python -m unittest discover -s backend/tests -v`。
4. 构建 x86_64 包。
5. 构建 aarch64 包。
6. 上传构建产物。
7. 为 `v1.0.002` 创建 Release。
8. 把 `.deb` 和 `.sha256` 上传到 Release。

查看进度：

```text
https://github.com/642671/qishi-note-vault-tos7/actions
```

### 11.4 查看 Release

```text
https://github.com/642671/qishi-note-vault-tos7/releases
```

Release 中应包含：

```text
qishi-note-vault_1.0.002_x86_64.deb
qishi-note-vault_1.0.002_x86_64.deb.sha256
qishi-note-vault_1.0.002_aarch64.deb
qishi-note-vault_1.0.002_aarch64.deb.sha256
```

## 12. GitHub Actions 失败时怎么办

打开：

```text
https://github.com/642671/qishi-note-vault-tos7/actions
```

点击失败的运行记录，再点击红色步骤查看日志。

常见问题：

### 测试失败

本地先运行：

```powershell
py -3 -m unittest discover -s backend/tests -v
```

修复后再提交。

### 版本不一致

检查：

```text
VERSION
config.ini
DEBIAN/control
```

三者必须一致。

### Release 已存在

如果已经存在相同标签的 Release，不要再创建同名 Release。

可以：

1. 删除错误标签和 Release 后重新发布。
2. 或者增加版本号，例如从 `1.0.002` 改为 `1.0.003`。

不要直接覆盖已经发布的安装包。

### 缺少 Python

本地测试先运行：

```powershell
py -3 --version
```

如果不可用，检查 Python 安装和 PATH。

## 13. 在 TOS/TNAS 上安装

### 13.1 推荐方式：App Center / 开发者平台

推荐通过 TOS App Center 或 Developer Platform 安装。

原因：

- 平台会创建 `qishi-note-vault` 专用用户。
- 平台会处理安装、启动和升级流程。
- 符合 TOS 权限模型。

### 13.2 测试环境直接安装

如果是在专用测试 NAS 上，可以先执行：

```bash
sudo dpkg -i qishi-note-vault_1.0.002_x86_64.deb
```

检查服务：

```bash
systemctl status qishinotevault-system.service
```

查看日志：

```bash
journalctl -u qishinotevault-system.service -n 100 --no-pager
```

检查 socket：

```bash
ls -l /var/api/qishi-note-vault.sock
```

如果直接 `dpkg -i` 后服务用户不存在，优先改用 App Center 安装，或在测试环境中确认平台是否已经创建用户。

## 14. 安装后验证清单

- [ ] TOS App Center 中显示“启思笔记”
- [ ] 应用版本显示为 `1.0.002`
- [ ] 应用可以打开 WebUI
- [ ] 可以创建、编辑和删除笔记
- [ ] 标题和正文搜索正常
- [ ] 标签、置顶、归档和回收站正常
- [ ] Markdown 预览正常
- [ ] 附件上传、下载和删除正常
- [ ] 导出 ZIP 备份成功
- [ ] 导入 ZIP 备份成功
- [ ] 服务重启后数据仍然存在
- [ ] 卸载后重新安装，已有数据按预期保留

## 15. 常见错误

### `python` 或 `py` 找不到

Windows 使用：

```powershell
py -3 --version
```

如果不可用，需要安装 Python，并重新打开 PowerShell。

### `ModuleNotFoundError: No module named 'qishi_note_vault'`

必须从仓库根目录运行测试：

```powershell
cd D:\111\dev-test\qishi-note-vault-tos7
py -3 -m unittest discover -s backend/tests -v
```

不要在 `backend` 子目录中直接运行。

### `config.ini is missing fields`

检查 `config.ini` 是否为合法 JSON，并且字段名全部使用小写。

不要添加注释，不要使用尾随逗号。

### 图标校验失败

图标必须：

- 使用 SVG。
- 不超过 50 KB。
- 不包含 `script`、`iframe`、`object`、`embed`。
- 不包含 `onclick`、`onload` 等事件属性。
- 不引用 `javascript:`、`file:` 等危险协议。

### 前端归档错误

`webui.bz2` 由 `build.py` 自动生成，不需要手工创建。

确保 `frontend` 中存在：

```text
index.html
app.js
styles.css
```

### 服务启动失败

检查：

```bash
systemctl status qishinotevault-system.service
journalctl -u qishinotevault-system.service -n 100 --no-pager
ls -ld /usr/local/qishi-note-vault
ls -ld /usr/local/qishi-note-vault/data
id qishi-note-vault
```

常见原因：

- 平台尚未创建应用用户。
- 数据目录权限不对。
- `/var/api` 不可写。
- Python 路径不正确。

## 16. 快速命令摘要

### 本地测试

```powershell
cd D:\111\dev-test\qishi-note-vault-tos7
py -3 -m unittest discover -s backend/tests -v
```

### 本地预览

```powershell
py -3 dev_server.py --port 4173
```

### 本地打包

```powershell
py -3 build.py --platform x86_64
py -3 build.py --platform aarch64
```

### 提交和自动发布

```powershell
git add .
git commit -m "release: v1.0.002"
git tag v1.0.002
git push origin main --tags
```

### 检查生成文件

```powershell
Get-ChildItem .\dist
Get-FileHash -Algorithm SHA256 .\dist\qishi-note-vault_1.0.002_x86_64.deb
```

### TOS 检查服务

```bash
systemctl status qishinotevault-system.service
journalctl -u qishinotevault-system.service -n 100 --no-pager
```
