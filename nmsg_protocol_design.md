# nmsg 通信协议设计文档 (Protocol Specification - v1.0)

## 1. 设计理念
采用 **"通用包头 (Envelope) + 类型特定载荷 (Payload)"** 的结构化 JSON 格式。旨在实现轻量级、可扩展且易于解析的 C/S 通信，解耦控制指令与大数据传输。

## 2. 通用包结构 (Universal Packet Envelope)

所有通过网络传输的 JSON 对象均包含以下基础字段：

| 字段名 | 类型 | 说明 |
| :--- | :--- | :--- |
| `v` | STRING | **Protocol Version** (例如 `"1.0"`)。用于版本兼容性检查。 |
| `type` | INTEGER/STRING | **Message Type**。标识消息类型 (`1`: Text, `2`: File, `3`: Packet)。 |
| `ts` | FLOAT/INT | **Timestamp**。Unix 时间戳，用于时序校验与排序。 |
| `payload` | OBJECT | **核心载荷**。根据 `type` 的不同，内部结构随之变化。 |

---

## 3. 消息类型详解 (Payload Specifications)

### Type 1: Text Message (文本消息)
用于客户端与服务端之间的纯文字沟通。

*   **Payload 结构**:
    ```json
    {
      "sender": "string",   // 发送者标识 (Client_ID 或 "SERVER")
      "receiver": "string", // 接收者标识
      "content": "string"   // 纯文本内容
    }
    ```
*   **示例**:
    ```json
    {
      "v": "1.0",
      "type": 1,
      "ts": 1776875100.0,
      "payload": {
        "sender": "client_192.168.1.5",
        "receiver": "SERVER",
        "content": "你好，请问有人在吗？"
      }
    }
    ```

### Type 2: File Transfer (文件传输控制)
负责文件传输过程的“握手”与“状态通知”。实际二进制流通过 HTTP Streaming 或 TCP Stream 承载。

*   **Payload 结构**:
    ```json
    {
      "file_id": "string",  // 本次传输的唯一 UUID
      "action": "string",   // 操作指令: "INIT" (开始), "PROGRESS" (进度), "COMPLETE" (完成), "ERROR" (失败)
      "meta": {             // 文件元数据
        "name": "string",
        "size": "integer",  // 字节大小
        "hash": "string"    // SHA256/MD5 校验值
      }
    }
    ```
*   **示例 (开始传输通知)**:
    ```json
    {
      "v": "1_0",
      "type": 2,
      "ts": 1776875105.0,
      "payload": {
        "file_id": "f-9a2b-4c3d",
        "action": "INIT",
        "meta": {
          "name": "report_2026.pdf",
          "size": 10485760,
          "hash": "e3b0c442..."
        }
      }
    }
    ```

### Type 3: Packet/Protocol Message (系统指令)
用于身份验证、心跳监测、配置变更等底层协议操作。

*   **Payload 结构**:
    ```json
    {
      "command": "string",  // 指令名: "AUTH", "HEARTBEAT", "CONFIG_UPDATE", "LOGOUT"
      "params": object      // 与指令相关的参数集合
    }
    ```
*   **示例 (身份认证)**:
    ```json
    {
      "v": "1.0",
      "type": 3,
      "ts": 1776875110.0,
      "payload": {
        "command": "AUTH",
        "params": {
          "token": "sk-a1_example_token"
        }
      }
    }
    ```

---

## 4. 设计优势总结
1.  **解耦性**: 通过 `file_id` 将 JSON 控制指令与二进制文件流物理分离，支持大文件异步传输。
2.  **安全性**: Type 3 的 `AUTH` 结合数据库中的 IP-Token 绑定实现零信任准入。
3.  **一致性**: 统一的 Envelope 降低了 Client/Server 端解析器的逻辑复杂度。
