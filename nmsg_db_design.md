# nmsg 数据库设计文档 (Database Design)

## 1. 设计目标
实现基于 SQLite 的轻量级存储，重点支持：
- **IP-Token 双重绑定校验**。
- **客户端在线状态追踪**。
- **消息历史与文件元数据管理**。
- **结构化与非结构化数据分离策略**。

## 2. 表结构定义

### A. `clients` 表 (身份与认证)
存储已验证客户端的身份信息及安全绑定关系。

| 字段名 | 类型 | 约束 | 说明 |
| :--- | :--- | :--- | :--- |
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | 内部唯一 ID |
| `client_name` | TEXT | NOT NULL | 客户端名称/别名 |
| `access_token` | TEXT | UNIQUE, NOT NULL | 身份验证 Token |
| `ip_address` | TEXT | NOT NULL | 绑定的合法 IP 地址 |
| `last_seen` | DATETIME | | 最后一次心跳时间 |
| `created_at` | DATETIME | DEFAULT CURRENT_TIMESTAMP | 注册时间 |

### B. `messages` 表 (通信记录)
存储所有类型的消息流（文本、文件通知、系统指令）。

| 字段名 | 类型 | 约束 | 说明 |
| :--- | :--- | :--- | :--- |
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | 消息唯一 ID |
| `sender_id` | INTEGER | FK (`clients.id`) | 发送者 ID (Server 端可为 NULL) |
| `receiver_id` | INTEGER | FK (`clients.id`) | 接收者 ID |
| `msg_type` | TEXT | CHECK(Type1, Type2, Type3) | 消息类型: Text, File, Packet |
| `content` | TEXT | | 消息正文或文件摘要 |
| `timestamp` | DATETIME | DEFAULT CURRENT_TIMESTAMP | 发送时间 |

### C. `file_metadata` 表 (文件元数据)
记录 Type 2 消息对应的物理文件信息。

| 字段名 | 类型 | 约束 | 说明 |
| :--- | :--- | :--- | :--- |
| `id` | INTEGER | PRIMARY KEY | 文件唯一 ID |
| `message_id` | INTEGER | FK (`messages.id`) | 关联的消息 ID |
| `file_name` | TEXT | | 原始文件名 |
| `file_size` | INTEGER | | 文件大小 (Bytes) |
| `storage_path` | TEXT | | 在 `storage/` 下的存储路径 |
| `checksum` | TEXT | | 文件校验哈希值 |

### D. `system_logs` 表 (审计日志)
记录系统运行的关键事件。

| 字段名 | 类型 | 约束 | 说明 |
| :--- | :--- | :--- | :--- |
| `id` | INTEGER | PRIMARY KEY | 日志 ID |
| `level` | TEXT | | INFO, WARNING, ERROR |
| `component` | TEXT | | 模块名 (auth, transport, etc.) |
| `event_desc` | TEXT | | 事件描述 |
| `timestamp` | DATETIME | DEFAULT CURRENT_TIMESTAMP | 发生时间 |

## 3. 核心查询逻辑 (SQL Snippets)

### 身份校验
```sql
SELECT id FROM clients WHERE access_token = ? AND ip_address = ?;
```

### 心跳更新
```sql
UPDATE clients SET last_seen = CURRENT_TIMESTAMP WHERE access_token = ?;
```

## 4. 优化建议
- **索引**: 在 `clients(access_token)` 和 `clients(ip_address)` 上建立复合或单列索引。
- **性能**: 使用 SQLite 的 `WAL` (Write-Ahead Logging) 模式以提升并发读写能力。
- **维护**: 定期清理 `system_logs` 与过期的 `messages` 记录。
