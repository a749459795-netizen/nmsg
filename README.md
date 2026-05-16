# nmsg

一个轻量级、专为 Windows 设计的 C/S 架构即时通讯与文件传输工具。

## 功能特性

- **Token + IP 双重绑定认证** — 永久生效的 Token，结合 IP 地址零信任验证
- **三种消息类型**：
  - Type 1：文本消息
  - Type 2：文件传输（支持流式传输、进度显示）
  - Type 3：系统指令（认证、心跳、配置更新）
- **混合存储策略** — SQLite 用于结构化数据，文件系统用于二进制文件
- **PyQt6 GUI** — 系统托盘通知、传输进度显示、持久化会话
- **轻量级协议** — 基于 JSON 的长度前缀包格式，易于解析和扩展

## 技术栈

- Python 3.x
- PyQt6 (GUI)
- SQLite (数据库)
- TCP Socket (通信)

## 项目结构

```
nmsg_project/
├── src/
│   ├── common/           # 共享模块
│   │   ├── protocol.py   # 协议包定义 (Envelope + Payload)
│   │   ├── database.py   # SQLite 操作
│   │   ├── storage.py    # 文件系统存储
│   │   └── exceptions.py
│   ├── server/           # 服务端
│   │   ├── server.py     # TCP 服务器主循环
│   │   ├── server_gui.py # PyQt6 管理界面
│   │   └── session.py    # 客户端会话管理
│   └── client/           # 客户端
│       ├── gui.py        # PyQt6 主界面
│       ├── connection.py # Socket 连接
│       ├── auth.py       # Token 持久化
│       └── file_transfer.py
├── server.py             # 服务端入口
└── client.py             # 客户端入口
```

## 快速开始

### 环境要求

- Python 3.x
- Windows 操作系统

### 安装依赖

```bash
pip install -r requirements.txt
```

### 运行

**服务端 (命令行模式):**
```bash
python server.py --host 0.0.0.0 --port 9000
```

**服务端 (图形界面模式):**
```bash
python server.py --gui
```

**客户端:**
```bash
python client.py
```

## 协议设计

所有数据包采用 **4字节长度前缀 + JSON** 格式：

| 类型 | 说明 | 关键载荷字段 |
|------|------|-------------|
| 1 | 文本消息 | `sender`, `receiver`, `content` |
| 2 | 文件传输 | `file_id`, `action` (INIT/PROGRESS/COMPLETE/ERROR), `meta` |
| 3 | 系统指令 | `command` (AUTH/HEARTBEAT/REGISTER/...), `params` |

## 认证流程

1. 新客户端 → 发送 `REGISTER` 命令 → 获取永久 Token
2. 已注册客户端 → 发送 `AUTH` 命令（携带 Token）
3. 服务端验证 **Token + IP** 绑定
4. 客户端每 60 秒发送 `HEARTBEAT` 保活

## 文件传输

1. 客户端发送 `Type 2 INIT` 数据包，包含 `file_id`、`name`、`size`、`hash`
2. 客户端直接通过 TCP Socket 流式传输原始字节
3. 服务端对输入流进行哈希校验，匹配则存储
4. 服务端返回 `COMPLETE` 确认或 `ERROR` 错误

## 数据库设计

| 表名 | 用途 |
|------|------|
| `clients` | 客户端身份、Token-IP 绑定 |
| `messages` | 消息历史记录 |
| `file_metadata` | 文件元数据 |
| `system_logs` | 审计日志 |

## 配置说明

客户端配置文件 `nmsg_client.json`（自动生成）:
```json
{
  "server_ip": "192.168.1.100",
  "server_port": 9000,
  "client_name": "MyPC",
  "access_token": "sk-xxx..."
}
```

## 许可证

MIT License