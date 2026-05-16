"""
nmsg Server - PyQt6 Admin GUI

Features:
- Start/Stop server
- Config: IP / Port
- Live client list (name, IP, last heartbeat)
- Live event / message log
- Per-client kick action
"""

import sys
import socket
import threading
import logging
import json
import pathlib
import signal
from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout,
    QTableWidget, QTableWidgetItem, QPushButton, QListWidget,
    QLabel, QLineEdit, QSpinBox, QTextEdit,
    QHeaderView, QTabWidget, QGroupBox,
    QMessageBox, QStyledItemDelegate, QDialog,
    QDialogButtonBox,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QColor, QTextCharFormat, QTextCursor

ROOT = pathlib.Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from src.server.server import NmsgServer
from src.common.database import Database
from src.common.protocol import PacketFactory

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("nmsg.server.gui")


# ─── Log Reader Thread ────────────────────────────────────────────────

class LogReaderThread(QThread):
    """Watch server's log table and emit only new lines to GUI."""
    sig_line = pyqtSignal(str)

    def __init__(self, db: Database):
        super().__init__()
        self.db = db
        self._running = True
        self._last_log_id = 0

    def run(self):
        while self._running:
            try:
                rows = self.db.get_recent_logs(50)
                # Emit only rows newer than the last seen ID
                for r in reversed(rows):
                    rid = r["id"]
                    if rid <= self._last_log_id:
                        continue
                    self._last_log_id = max(self._last_log_id, rid)
                    self.sig_line.emit(
                        f"[{r['timestamp']}] [{r['level']}] [{r['component']}] {r['event_desc']}"
                    )
            except Exception:
                pass
            QThread.msleep(2000)

    def stop(self):
        self._running = False


# ─── Server GUI ──────────────────────────────────────────────────────

class ServerAdminWindow(QWidget):
    sig_start = pyqtSignal(str, int)
    sig_stop = pyqtSignal()
    sig_kick = pyqtSignal(str)  # token

    def __init__(self):
        super().__init__()
        self.server: NmsgServer | None = None
        self._log_timer: QTimer | None = None

        self.setWindowTitle("nmsg 服务管理")
        self.setMinimumSize(800, 550)
        self.setStyleSheet(self._style())

        self._init_ui()
        self._restore_config()

    # ─── UI Setup ────────────────────────────────────────────────────

    def _style(self) -> str:
        return """
            QWidget { background: #1e1e1e; color: #e0e0e0; font-family: 'Segoe UI', sans-serif; font-size: 13px; }
            QGroupBox { border: 1px solid #444; margin-top: 10px; padding-top: 10px; }
            QGroupBox::title { color: #aaa; }
            QPushButton {
                background: #3c3c3c; color: #e0e0e0; border: 1px solid #555;
                padding: 6px 16px; border-radius: 3px;
            }
            QPushButton:hover { background: #4a4a4a; }
            QPushButton:pressed { background: #2a2a2a; }
            QPushButton:disabled { background: #2a2a2a; color: #666; }
            QLineEdit, QSpinBox {
                background: #2d2d2d; color: #e0e0e0; border: 1px solid #444;
                padding: 4px; border-radius: 3px;
            }
            QTextEdit {
                background: #141414; color: #cccccc; border: 1px solid #333;
                font-family: 'Consolas', monospace; font-size: 12px;
            }
            QTableWidget {
                background: #141414; color: #e0e0e0; gridline-color: #333;
                border: 1px solid #333;
            }
            QTableWidget::item { padding: 4px; }
            QHeaderView::section {
                background: #2a2a2a; color: #aaa; padding: 4px;
                border: 1px solid #333;
            }
            QLabel { color: #e0e0e0; }
            QTabWidget::pane { border: 1px solid #444; }
        """

    def _init_ui(self):
        main = QVBoxLayout(self)

        # ── Top: Server Control ──────────────────────────────────────
        ctrl = QGroupBox("服务器控制")
        ctrl_layout = QGridLayout(ctrl)

        lbl_ip = QLabel("监听 IP：")
        self.ip_edit = QLineEdit("0.0.0.0")
        lbl_port = QLabel("端口：")
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1024, 65535)
        self.port_spin.setValue(9000)

        self.start_btn = QPushButton("▶ 启动服务器")
        self.start_btn.setStyleSheet("background: #2e7d32;")
        self.start_btn.clicked.connect(self._on_start)

        self.stop_btn = QPushButton("■ 停止服务器")
        self.stop_btn.setDisabled(True)
        self.stop_btn.clicked.connect(self._on_stop)

        self.status_lbl = QLabel("● 已停止")
        self.status_lbl.setStyleSheet("color: #F44336; font-weight: bold;")

        ctrl_layout.addWidget(lbl_ip, 0, 0)
        ctrl_layout.addWidget(self.ip_edit, 0, 1)
        ctrl_layout.addWidget(lbl_port, 0, 2)
        ctrl_layout.addWidget(self.port_spin, 0, 3)
        ctrl_layout.addWidget(self.start_btn, 0, 4)
        ctrl_layout.addWidget(self.stop_btn, 0, 5)
        ctrl_layout.addWidget(self.status_lbl, 0, 6, Qt.AlignmentFlag.AlignHCenter)
        ctrl_layout.setColumnStretch(1, 1)

        main.addWidget(ctrl)

        # ── Tabs ───────────────────────────────────────────────────────
        tabs = QTabWidget()

        # Tab 1: Clients
        self.client_table = QTableWidget(0, 7)
        self.client_table.setHorizontalHeaderLabels(["名称", "Token（隐藏）", "IP", "最后活跃", "最后消息", "对话", "操作"])
        self.client_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.client_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.client_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.client_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self.client_table.setColumnWidth(5, 60)
        self.client_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        self.client_table.setColumnWidth(6, 60)
        self.client_table.verticalHeader().setVisible(False)
        self.client_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.client_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        # Tab 2: Log
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)

        tabs.addTab(self.client_table, "已连接客户端")
        tabs.addTab(self.log_view, "事件日志")
        main.addWidget(tabs, stretch=1)

        # ── Bottom: Refresh / DB path ───────────────────────────────────
        bottom = QHBoxLayout()
        self.db_path_lbl = QLabel("数据库：—")
        bottom.addWidget(self.db_path_lbl)
        bottom.addStretch()
        self.refresh_btn = QPushButton("🔄 刷新客户端")
        self.refresh_btn.clicked.connect(self._refresh_clients)
        self.clear_log_btn = QPushButton("清空日志")
        self.clear_log_btn.clicked.connect(lambda: self.log_view.clear())
        self.add_client_btn = QPushButton("+ 添加客户端")
        self.add_client_btn.setStyleSheet("background: #1565C0;")
        self.add_client_btn.clicked.connect(self._on_add_client)
        bottom.addWidget(self.add_client_btn)
        bottom.addWidget(self.refresh_btn)
        bottom.addWidget(self.clear_log_btn)
        main.addLayout(bottom)

        self.setLayout(main)

    # ─── Server Control ────────────────────────────────────────────────

    def _on_start(self):
        ip = self.ip_edit.text().strip()
        port = self.port_spin.value()
        self._save_config(ip, port)

        try:
            self.server = NmsgServer(
                host=ip,
                port=port,
                db_path="nmsg.db",
                storage_root="storage",
            )
            # Redirect server log to our log view
            server_log = logging.getLogger("nmsg.server")
            root_handler = logging.getLogger().handlers[0] if logging.getLogger().handlers else None
            handler = ServerLogHandler(self._append_log)
            if root_handler:
                handler.setFormatter(root_handler.formatter)
            server_log.addHandler(handler)

            t = threading.Thread(target=self.server.start, daemon=True)
            t.start()

            self.start_btn.setDisabled(True)
            self.stop_btn.setDisabled(False)
            self.ip_edit.setDisabled(True)
            self.port_spin.setDisabled(False)
            self.status_lbl.setText(f"● 运行中 {ip}:{port}")
            self.status_lbl.setStyleSheet("color: #4CAF50; font-weight: bold;")
            self.db_path_lbl.setText(f"数据库：{pathlib.Path('nmsg.db').resolve()}")

            self._append_log(f"[INFO] Server started on {ip}:{port}")
            self._refresh_clients()

        except Exception as e:
            import traceback
            QMessageBox.critical(
                self, "启动错误",
                f"{type(e).__name__}: {e}\n\n{traceback.format_exc()}"
            )

    def _on_stop(self):
        if self.server:
            self.server.stop()
            self.server = None
        self.start_btn.setDisabled(False)
        self.stop_btn.setDisabled(True)
        self.ip_edit.setDisabled(False)
        self.port_spin.setDisabled(False)
        self.status_lbl.setText("● 已停止")
        self.status_lbl.setStyleSheet("color: #F44336; font-weight: bold;")
        self._append_log("[INFO] Server stopped")

    def _refresh_clients(self):
        if not self.server:
            return
        sessions = self.server.sessions.list_all()
        clients = self.server.db.list_clients() if self.server else []

        # Merge: show registered clients, mark online ones
        online_tokens = {s.token for s in sessions}
        self.client_table.setRowCount(len(clients))

        for row, c in enumerate(clients):
            name_item = QTableWidgetItem(c["client_name"])
            token_str = c["access_token"]
            token_item = QTableWidgetItem(token_str[:12] + "***")
            ip_item = QTableWidgetItem(c["ip_address"])
            last_seen = c["last_seen"] or "—"
            last_item = QTableWidgetItem(str(last_seen))

            is_online = token_str in online_tokens
            color = QColor("#4CAF50") if is_online else QColor("#666")
            for item in [name_item, token_item, ip_item, last_item]:
                item.setForeground(color)

            self.client_table.setItem(row, 0, name_item)
            self.client_table.setItem(row, 1, token_item)
            self.client_table.setItem(row, 2, ip_item)
            self.client_table.setItem(row, 3, last_item)

            # Col 4: last message
            last_msg = self.server.db.get_client_last_message_any(c["id"])
            if last_msg:
                content = (last_msg["content"] or "")[:30]
                ts = last_msg["timestamp"] or ""
                msg_text = f"{content}  [{ts[-8:-3] if ts else ''}]"
            else:
                msg_text = "—"
            msg_item = QTableWidgetItem(msg_text)
            msg_item.setForeground(color)
            self.client_table.setItem(row, 4, msg_item)

            # Col 5: chat button
            chat_btn = QPushButton("对话")
            chat_btn.setFixedWidth(60)
            chat_btn.setStyleSheet("background: #1565C0; padding: 2px; font-size: 11px;")
            client_id_for_chat = c["id"]
            chat_btn.clicked.connect(lambda _, cid=client_id_for_chat: self._open_chat(cid))
            self.client_table.setCellWidget(row, 5, chat_btn)

            # Col 6: kick button
            kick_btn = QPushButton("踢出")
            kick_btn.setFixedWidth(60)
            kick_btn.setStyleSheet("background: #c62828; padding: 2px; font-size: 11px;")
            token_for_kick = token_str
            kick_btn.clicked.connect(lambda _, t=token_for_kick: self._kick_client(t))
            self.client_table.setCellWidget(row, 6, kick_btn)

        self.client_table.resizeColumnsToContents()

    def _kick_client(self, token: str):
        if not self.server:
            return
        # Find client_id from token
        client_id = self.server.db.get_client_id_by_token(token)
        self.server.sessions.remove(token)
        if client_id is not None:
            self.server.db.delete_client(client_id)
        self._append_log(f"[INFO] 已踢出并删除：{token[:12]}...")
        self._refresh_clients()

    def _open_chat(self, client_id: int):
        if not self.server:
            return
        messages = self.server.db.get_messages_for_client(client_id, limit=100)
        client_info = self.server.db.get_client_info(client_id)
        dlg = ChatDialog(self, client_info, messages)
        dlg.exec()


    def _on_add_client(self):
        if not self.server:
            QMessageBox.warning(self, "服务器未运行", "请先启动服务器。")
            return
        dlg = AddClientDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            name, ip = dlg.get_values()
            try:
                token, client_id = self.server.db.register_client(name, ip)
                self._append_log(f"[INFO] 添加客户端：{name}（{ip}），Token：{token}")
                QMessageBox.information(
                    self, "客户端已添加",
                    f"客户端名称：{name}\n"
                    f"绑定 IP：{ip}\n"
                    f"Token：{token}\n\n"
                    f"请将此 Token 告知客户端管理员。"
                )
                self._refresh_clients()
            except Exception as e:
                QMessageBox.critical(self, "添加失败", str(e))

    # ─── Log ─────────────────────────────────────────────────────────

    def _append_log(self, text: str):
        # Auto-invoke on GUI thread
        QTimer.singleShot(0, lambda: self._do_append_log(text))

    def _do_append_log(self, text: str):
        color = "#cccccc"
        if "[ERROR]" in text:
            color = "#ef5350"
        elif "[WARNING]" in text:
            color = "#FFB74D"
        elif "[INFO]" in text:
            color = "#81C784"

        self.log_view.moveCursor(QTextCursor.MoveOperation.End)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        self.log_view.setCurrentCharFormat(fmt)
        self.log_view.append(text)
        self.log_view.moveCursor(QTextCursor.MoveOperation.End)

    # ─── Config Persistence ───────────────────────────────────────────

    def _config_path(self) -> pathlib.Path:
        return pathlib.Path("nmsg_server_config.json")

    def _save_config(self, ip: str, port: int):
        try:
            self._config_path().write_text(json.dumps({"ip": ip, "port": port}))
        except Exception:
            pass

    def _restore_config(self):
        try:
            p = self._config_path()
            if p.exists():
                d = json.loads(p.read_text())
                self.ip_edit.setText(d.get("ip", "0.0.0.0"))
                self.port_spin.setValue(d.get("port", 9000))
        except Exception:
            pass

    def closeEvent(self, event):
        self._on_stop()
        event.accept()


# ─── Add Client Dialog ────────────────────────────────────────────────

class AddClientDialog(QDialog):
    _style = """
        QWidget { background: #252525; color: #e0e0e0; font-family: 'Segoe UI', sans-serif; font-size: 13px; }
        QLabel { color: #e0e0e0; }
        QLineEdit { background: #2d2d2d; color: #e0e0e0; border: 1px solid #444; padding: 4px; border-radius: 3px; }
        QPushButton { background: #3c3c3c; color: #e0e0e0; border: 1px solid #555; padding: 5px 16px; border-radius: 3px; }
        QPushButton:hover { background: #4a4a4a; }
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("添加客户端")
        self.setModal(True)
        self.setStyleSheet(self._style)
        layout = QFormLayout(self)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("例如：办公室电脑")

        self.ip_edit = QLineEdit("0.0.0.0")
        self.ip_edit.setPlaceholderText("允许连接的 IP，留空表示任意 IP")

        layout.addRow("客户端名称：", self.name_edit)
        layout.addRow("绑定 IP：", self.ip_edit)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addRow(btns)

    def get_values(self):
        name = self.name_edit.text().strip()
        ip = self.ip_edit.text().strip() or "0.0.0.0"
        return name, ip


_CHAT_DIALOG_STYLE = """
    QWidget { background: #1e1e1e; color: #e0e0e0; font-family: 'Segoe UI', sans-serif; font-size: 13px; }
    QPushButton { background: #3c3c3c; color: #e0e0e0; border: 1px solid #555; padding: 6px 20px; border-radius: 3px; }
    QPushButton:hover { background: #4a4a4a; }
"""


# ─── Chat Dialog ─────────────────────────────────────────────────────

class ChatDialog(QDialog):
    def __init__(self, parent, client_info: dict, messages: list):
        super().__init__(parent)
        self.setWindowTitle(f"与 {client_info.get('client_name', '?')} 的对话")
        self.setMinimumSize(600, 400)
        self.setStyleSheet(_CHAT_DIALOG_STYLE)
        layout = QVBoxLayout(self)

        # Message list
        self.client_id = client_info.get("id")
        self.msg_list = QListWidget()
        self.msg_list.setStyleSheet("""
            QListWidget { background: #141414; color: #e0e0e0; border: none; font-family: 'Segoe UI'; font-size: 13px; }
            QListWidget::item { padding: 4px; }
        """)

        # Load messages
        clients = parent.server.db.list_clients() if hasattr(parent, 'server') and parent.server else {}
        for msg in reversed(messages):
            sender_id = msg.get("sender_id")
            sender_name = msg.get("sender_name") or "未知"
            content = msg.get("content") or ""
            ts = msg.get("timestamp") or ""
            if sender_id == client_info.get("id"):
                prefix = "▼ 你"
            elif sender_id is None or sender_id == 0:
                prefix = "● 系统"
            else:
                prefix = f"▲ {sender_name}"
            item_text = f"[{ts[11:19] if len(ts) > 11 else ts}] {prefix}\n{content}"
            self.msg_list.addItem(item_text)

        layout.addWidget(self.msg_list)

        # Input area
        input_layout = QHBoxLayout()
        self.input_edit = QTextEdit()
        self.input_edit.setPlaceholderText("输入消息...")
        self.input_edit.setFixedHeight(80)
        self.input_edit.setStyleSheet("background: #2d2d2d; color: #e0e0e0; border: 1px solid #444; padding: 4px; border-radius: 3px; font-size: 13px;")
        self.send_btn = QPushButton("发送")
        self.send_btn.setStyleSheet("background: #1565C0; padding: 4px 16px;")
        self.send_btn.clicked.connect(self._on_send)
        input_layout.addWidget(self.input_edit)
        input_layout.addWidget(self.send_btn)

        bottom_layout = QHBoxLayout()
        refresh_btn = QPushButton("🔄 刷新")
        refresh_btn.setStyleSheet("padding: 4px 12px;")
        refresh_btn.clicked.connect(self._on_refresh)
        bottom_layout.addWidget(refresh_btn)
        bottom_layout.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        bottom_layout.addWidget(close_btn)

        layout.addLayout(input_layout)
        layout.addLayout(bottom_layout)
        self.setLayout(layout)

    def _on_send(self):
        text = self.input_edit.toPlainText().strip()
        if not text:
            return
        try:
            session = self.parent().server.sessions.get_by_client_id(self.client_id)
            if not session:
                QMessageBox.warning(self, "发送失败", "该客户端当前未连接")
                return
            pkt = PacketFactory.text("SERVER", session.client_name, text)
            self.parent().server._send_packet(session.socket, pkt)
            # Also save to DB so refresh shows it
            self.parent().server.db.save_message_no_fk(
                sender_id=0, receiver_id=self.client_id, msg_type="Text", content=text
            )
            self.msg_list.addItem(f"[手动] ▼ 服务端\n{text}")
            self.msg_list.scrollToBottom()
            self.input_edit.clear()
        except Exception as e:
            import traceback
            QMessageBox.warning(self, "发送失败", f"{type(e).__name__}: {e}\n\n{traceback.format_exc()}")

    def _on_refresh(self):
        try:
            messages = self.parent().server.db.get_messages_for_client(self.client_id, limit=100)
            self.msg_list.clear()
            for msg in reversed(messages):
                sender_id = msg.get("sender_id")
                sender_name = msg.get("sender_name") or "未知"
                content = msg.get("content") or ""
                ts = msg.get("timestamp") or ""
                if sender_id == self.client_id:
                    prefix = "▼ 你"
                elif sender_id is None or sender_id == 0:
                    prefix = "● 系统"
                else:
                    prefix = f"▲ {sender_name}"
                self.msg_list.addItem(f"[{ts[11:19] if len(ts) > 11 else ts}] {prefix}\n{content}")
        except Exception as e:
            QMessageBox.warning(self, "刷新失败", str(e))

    _style = """
        QWidget { background: #1e1e1e; color: #e0e0e0; font-family: 'Segoe UI', sans-serif; font-size: 13px; }
        QPushButton { background: #3c3c3c; color: #e0e0e0; border: 1px solid #555; padding: 6px 20px; border-radius: 3px; }
        QPushButton:hover { background: #4a4a4a; }
    """


# ─── Server Log Handler ──────────────────────────────────────────────

class ServerLogHandler(logging.Handler):
    def __init__(self, callback, level=logging.NOTSET):
        super().__init__(level)
        self._callback = callback

    def emit(self, record: logging.LogRecord):
        msg = self.format(record)
        self._callback(msg)


# ─── Entry Point ──────────────────────────────────────────────────────

from PyQt6.QtWidgets import QApplication


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)

    # Handle Ctrl+C
    def sigint_handler():
        app.quit()
    signal.signal(signal.SIGINT, sigint_handler)

    w = ServerAdminWindow()
    w.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
