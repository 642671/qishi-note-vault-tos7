# Backend API

所有路径都可以通过 TOS 代理访问，前缀为：

```text
/v2/proxy/qishi-note-vault
```

## 状态

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | 服务和 Python 版本 |
| GET | `/info` | 应用信息与统计 |
| GET | `/stats` | 笔记数量统计 |

## 笔记

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/notes?query=&tag=&view=` | 搜索和筛选笔记 |
| POST | `/notes` | 创建笔记 |
| GET | `/notes/{id}` | 获取一条笔记及附件 |
| PUT | `/notes/{id}` | 更新标题、正文、标签或状态 |
| DELETE | `/notes/{id}` | 移入回收站 |
| DELETE | `/notes/{id}?permanent=true` | 永久删除 |
| POST | `/notes/{id}/pin` | 设置置顶 |
| POST | `/notes/{id}/archive` | 设置归档 |
| POST | `/notes/{id}/restore` | 从回收站恢复 |

创建或更新笔记的 JSON：

```json
{
  "title": "Python notes",
  "body": "# Heading\n\nMarkdown body",
  "tags": ["python", "study"]
}
```

## 标签

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/tags` | 标签及可用笔记数量 |

## 附件

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/attachments` | 上传附件，使用 multipart 字段 `note_id` 和 `file` |
| GET | `/attachments/{id}` | 下载或预览附件 |
| DELETE | `/attachments/{id}` | 删除附件 |

每个附件最大 8 MB。

## 备份

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/backup/export` | 下载包含笔记和附件的 ZIP |
| POST | `/backup/import` | 从 multipart 字段 `file` 导入 ZIP |

备份 ZIP 包含 `notes.json` 和 `attachments/`。
