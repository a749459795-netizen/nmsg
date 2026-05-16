# nmsg 项目需求规格说明书 (Final Version - v1.1)

## 1. 项目概述
**nmsg** 是一个基于 Python 开发的、专为 Windows 环境设计的局                构内 C/S 架构通信与文件传输工具。其核心逻辑是通过“Token + IP”的双重绑定机制实现轻量级身份验证，并采用结构化（数据库）与非结构化（文件系统）分离的存储策略。

## 2. 技术栈定义 (Tech Stack)
*   **开发语言:** Python 3.x
*   **运行平台:** Windows (仅限)
*   **架构模式:** Client/Server (C/S)
*   **GUI 框架:** PySide6 或 Tkinter
*   **通信协议:** TCP Socket / HTTP Streaming (用于大文件流式传输)
*   **数据库:** SQLite (结构化数据存储)
*   **文件系统管理:** 基于 IP 地址层级的本地目录树

## 3. 核心功能模块设计

### A. 安全与身份验证机制 (Security & Authentication)
1.  **Token 分发流程:** 客户端接入时，Server 生成唯一的 `Access_Token`。
2.  **永久有效性:** Token 一经分发，除非手动注销，否则永久有效。
3.  **IP-Token 双重绑定:** Server 将 `(Access_Token, Client_IP)` 映射关系持久化存储在 SQLite 中。
4.  **强制校验机制:** 每次消息收发请求必须携带 Token；Server 需验证：`Token 是否存在` $\land$ `Token 绑定的 IP == 当前请求来源 IP`。
5.  **客户端认证重连:** 客户端每 **1 分钟** 与 Server 进行一次在线身份认证（Heartbeat/Auth Check），确保连接有效性。
6.  **持久化登录:** 客户端重启后，自动加载本地记录的配置与 Token，无需再次申请。

### B. 数据存储架构 (Data Storage Strategy)
1.  **结构化数据 (SQLite):**
    *   存储内容: 客户端列表、Token 映射表、消息历史、文件元数据、系统日志。
2.  **非结构化数据 (File System):**
    *   **存储逻辑:** Server 建立 `storage/` 根目录。
    *   **层级隔离:** 根据客户端 IP 地址自动创建二级子目录（例如：`storage/uploads/192.168.1.5/`）。
    *   **文件限制:** 单个传输文件上限设置为 **100MB**。

### C. 客户端功能需求 (Client Detailed Requirements)
1.  **UI/UX 设计:**
    *   **配置入口:** 在界面右下角放置【设置】按钮，提供 Server IP 和 Port 的输入窗口；首次输入后自动记录到本地配置文件。
    *   **网络状态指示:** 在设置图标上方设置【网络状态图标】，实时反映当前与 Server 的连接状态（在线/离线/认证中）。
    *   **系统通知:** 集成 **Windows 系统托盘通知 (System Tray Notification)**，用于接收来自 Server 的重要消息或文件到达提醒。
2.  **消息类型支持:** 客户端消息流主要分为三类：
    *   **Type 1 - 文本 (Text):** 普通文字沟通。
    
    *   **Type 2 - 文件 (File):** 文件传输通知及下载/上传操作。
    
    *   **Type 3 - 报文 (Packet/Protocol Message):** 系统级指令、状态更新或协议定义的特定数据包。
3.  **文件传输监控:** 在大文件流式传输期间，界面需实时展示：**当前速度 (Speed)**、**传输进度 (%)** 以及 **预计剩余时间 (ETA)**。
4.  **本地历史记录:** 客户端具备本地持久化能力，支持查看离线状态下的历史聊天记录。

## 4. 待办事项 (Roadmap)
*   [ ] 设计数据库 Schema (SQLite Tables)
*   [ ] 设计 Client/Server 通信协议格式 (JSON Packet Structure for Types 1, 2, 3)
*   [ ] 实现 Server 端 Token 分发与 IP 校验逻辑
*   [ ] 实现 Client 端 UI 与 Streaming 传输模块
