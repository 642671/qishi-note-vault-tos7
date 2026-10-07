# 目录结构与运行流程

## 开发目录

```text
backend/
├── qishi-note-vault-service
├── qishi_note_vault/
│   ├── __init__.py
│   ├── database.py
│   ├── notes.py
│   └── server.py
└── tests/
```

`database.py` 只处理 SQLite 和数据结构；`notes.py` 负责业务操作、附件和备份；`server.py` 负责 HTTP 路由。每一层都可以单独阅读和测试。

## 安装后的目录

```text
/usr/local/qishi-note-vault/
├── config.ini
├── bin/qishi-note-vault-service
├── lib/qishi_note_vault/
├── qishi-note-vault.lang
├── qishi-note-vault.env
├── images/icons/qishi-note-vault.svg
├── init.d/qishinotevault-system.service
├── webui.bz2
├── data/
└── logs/
```

`/usr/local/qishi-note-vault/` 是应用看到的逻辑路径，平台会将其映射到用户安装时选择的存储卷。

## 打包流程

1. 读取并校验 `config.ini`。
2. 校验 14 种语言和 SVG 图标。
3. 将 Python 模块复制到安装包的 `lib/`。
4. 将前端压缩为 TOS 要求的 `webui.bz2`。
5. 同步版本号和架构到 `DEBIAN/control`。
6. 生成 `control.tar.gz`、`data.tar.gz` 和 Debian `ar` 容器。
7. 重新读取生成的 `.deb`，检查版本、架构、文件路径、可执行权限和前端归档。

## 运行流程

```text
TOS App Center
  -> systemd 启动 qishinotevault-system.service
  -> Python 创建 /var/api/qishi-note-vault.sock
  -> TOS 将 /v2/proxy/qishi-note-vault/ 代理到服务
  -> SQLite 保存笔记和标签
  -> attachments/ 保存附件
```

## Python 学习顺序

1. `database.py`：SQLite、参数化 SQL、事务和上下文管理器。
2. `notes.py`：路径校验、文件操作、ZIP 归档和业务规则。
3. `server.py`：HTTP 路由、JSON、multipart 和 Unix Socket。
4. `backend/tests/`：如何使用 `unittest` 验证数据层、业务层和接口层。
5. `build.py`：如何只用标准库生成标准 Debian 包。
