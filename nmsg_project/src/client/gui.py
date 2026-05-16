"""
nmsg Client - Main PyQt6 GUI Window

Features:
- Server connection settings (IP/Port) in bottom-right [Settings] popup
- Network status indicator icon
- Message list (text + file notifications)
- Message input area with Send button
- File attach button
- File transfer progress display
- System tray icon and notifications
- 自动重连
"""

import sys
import os
import json
import time
import threading
import logging
import tempfile
import hashlib
import uuid
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QLineEdit,
    QPushButton, QLabel, QTextEdit, QComboBox, QSpinBox,
    QDialog, QDialogButtonBox, QFormLayout, QFileDialog,
    QMessageBox, QProgressBar, QSystemTrayIcon, QMenu,
    QGraphicsScene, QGraphicsView, QGraphicsTextItem,
    QSplitter, QStyle, QSizePolicy,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QSize
from PyQt6.QtGui import (
    QIcon, QAction, QTextCursor,
    QFont, QPalette, QColor,
)

from .connection import Connection
from .auth import ClientAuth
from .file_transfer import FileUploader
from ..common.protocol import (
    Packet, PacketType, PacketFactory,
    PacketCommand, FileAction,
)
from ..common.exceptions import AuthError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("nmsg.client.gui")


# ─── Network Status Icons (colored dots) ──────────────────────────────

STATUS_COLOR_ONLINE = "#4CAF50"
STATUS_COLOR_OFFLINE = "#F44336"
STATUS_COLOR_AUTHING = "#FF9800"
STATUS_COLOR_TRANSFERRING = "#2196F3"


# ─── Settings Dialog ─────────────────────────────────────────────────

class SettingsDialog(QDialog):
    def __init__(self, parent, current_ip: str, current_port: int, current_token: str = ""):
        super().__init__(parent)
        self.setWindowTitle("连接设置")
        self.setModal(True)
        layout = QFormLayout(self)

        self.ip_edit = QLineEdit(current_ip)
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1024, 65535)
        self.port_spin.setValue(current_port)
        self.token_edit = QLineEdit(current_token)
        self.token_edit.setPlaceholderText("请输入服务器分配的 Token")

        layout.addRow("服务器 IP：", self.ip_edit)
        layout.addRow("服务器端口：", self.port_spin)
        layout.addRow("Token：", self.token_edit)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addRow(btns)

    def get_values(self):
        return (
            self.ip_edit.text().strip(),
            self.port_spin.value(),
            self.token_edit.text().strip(),
        )


# ─── Transfer Progress Dialog ───────────────────────────────────────

class TransferDialog(QDialog):
    def __init__(self, parent, filename: str, direction: str = "Uploading"):
        super().__init__(parent)
        self.setWindowTitle(f"{direction} {filename}")
        self.setMinimumWidth(400)
        layout = QVBoxLayout(self)

        self.label = QLabel(f"{direction} {filename}...")
        self.progress = QProgressBar()
        self.speed_label = QLabel("速度：—")
        self.eta_label = QLabel("剩余时间：—")
        self.cancel_btn = QPushButton("取消")

        layout.addWidget(self.label)
        layout.addWidget(self.progress)
        layout.addWidget(self.speed_label)
        layout.addWidget(self.eta_label)
        layout.addWidget(self.cancel_btn)

        self.cancel_btn.clicked.connect(self.reject)
        self.setModal(True)

    def update_progress(self, done: int, total: int, speed: float):
        pct = int(100 * done / total) if total > 0 else 0
        self.progress.setValue(pct)
        self.speed_label.setText(f"Speed: {self._format_speed(speed)}")
        remaining = (total - done) / speed if speed > 0 else 0
        self.eta_label.setText(f"ETA: {int(remaining)}s")

    @staticmethod
    def _format_speed(bps: float) -> str:
        if bps < 1024:
            return f"{bps:.0f} B/s"
        elif bps < 1024 * 1024:
            return f"{bps/1024:.1f} KB/s"
        else:
            return f"{bps/1024/1024:.1f} MB/s"


# ─── Main Window ────────────────────────────────────────────────────

class NmsgMainWindow(QWidget):
    """
    Main application window for the nmsg client.
    """

    RECONNECT_DELAY = 5      # seconds

    # Signals from background threads to GUI
    sig_connected = pyqtSignal()
    sig_disconnected = pyqtSignal()
    sig_auth_ok = pyqtSignal()
    sig_auth_failed = pyqtSignal(str)
    sig_message = pyqtSignal(dict)
    sig_file_received = pyqtSignal(dict)
    sig_transfer_complete = pyqtSignal(str, bool, str)  # file_id, success, message

    def __init__(self):
        super().__init__()
        self.auth = ClientAuth()
        self.conn: Connection | None = None
        self._uploader: FileUploader | None = None
        self._reconnect_timer: QTimer | None = None
        self._pending_file_id: str | None = None
        self._active_transfers: dict[str, TransferDialog] = {}
        self._message_history: list[dict] = []  # local message store for refresh

        self._init_ui()
        self._init_tray()
        self._apply_auth_config()
        self._connect_signals()

        # Restore window geometry
        self._restore_geometry()

    # ─── UI Setup ────────────────────────────────────────────────────

    def _init_ui(self):
        self.setWindowTitle("nmsg 客户端")
        self.setMinimumSize(600, 450)
        self.setStyleSheet(self._dark_style())

        # Top bar: status + controls
        top_bar = QHBoxLayout()

        self.status_label = QLabel("⚫ 离线")
        self.status_label.setStyleSheet(f"color: {STATUS_COLOR_OFFLINE}; font-weight: bold;")
        top_bar.addWidget(self.status_label)

        top_bar.addStretch()

        settings_btn = QPushButton("⚙ 设置")
        settings_btn.setFixedSize(80, 28)
        settings_btn.clicked.connect(self._open_settings)
        top_bar.addWidget(settings_btn)

        refresh_msg_btn = QPushButton("🔄 刷新消息")
        refresh_msg_btn.setFixedSize(90, 28)
        refresh_msg_btn.clicked.connect(self._on_refresh_messages)
        top_bar.addWidget(refresh_msg_btn)

        # Message list
        self.msg_list = QListWidget()
        self.msg_list.setStyleSheet("""
            QListWidget { background: #1e1e1e; color: #e0e0e0; border: none; }
            QListWidget::item { padding: 4px; }
        """)

        # Bottom area: input + send
        bottom_bar = QHBoxLayout()
        self.attach_btn = QPushButton("📎 附件")
        self.attach_btn.setFixedSize(70, 32)
        self.attach_btn.clicked.connect(self._attach_file)

        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("输入消息...")
        self.input_field.setStyleSheet("background: #2d2d2d; color: #e0e0e0; border: 1px solid #444; padding: 4px;")
        self.input_field.returnPressed.connect(self._send_text)

        self.send_btn = QPushButton("发送")
        self.send_btn.setFixedSize(60, 32)
        self.send_btn.clicked.connect(self._send_text)

        bottom_bar.addWidget(self.attach_btn)
        bottom_bar.addWidget(self.input_field)
        bottom_bar.addWidget(self.send_btn)

        # Main layout
        main = QVBoxLayout(self)
        main.addLayout(top_bar)
        main.addWidget(self.msg_list, stretch=1)
        main.addLayout(bottom_bar)

        self.setLayout(main)

    def _dark_style(self) -> str:
        return """
            QWidget { background: #252525; color: #e0e0e0; font-family: 'Segoe UI', sans-serif; font-size: 13px; }
            QPushButton { background: #3c3c3c; color: #e0e0e0; border: 1px solid #555; padding: 5px 12px; border-radius: 3px; }
            QPushButton:hover { background: #4a4a4a; }
            QPushButton:pressed { background: #2a2a2a; }
            QLineEdit { background: #2d2d2d; color: #e0e0e0; border: 1px solid #444; padding: 5px; border-radius: 3px; }
            QLabel { color: #e0e0e0; }
        """

    def _init_tray(self):
        self.tray = QSystemTrayIcon(self)
        self.tray.setToolTip("nmsg Client")
        self.tray.activated.connect(self._tray_activated)

        menu = QMenu()
        menu.addAction("Show", self.show)
        menu.addAction("Quit", self._quit_app)
        self.tray.setContextMenu(menu)

        self.tray.show()

    def _connect_signals(self):
        self.sig_connected.connect(self._on_connected)
        self.sig_disconnected.connect(self._on_disconnected)
        self.sig_auth_ok.connect(self._on_auth_ok)
        self.sig_auth_failed.connect(self._on_auth_failed)
        self.sig_message.connect(self._on_message)
        self.sig_file_received.connect(self._on_file_received)
        self.sig_transfer_complete.connect(self._on_transfer_complete)

    # ─── Auth & Connection ───────────────────────────────────────────

    def _apply_auth_config(self):
        if self.auth.server_ip:
            self._set_status(f"🟠 正在连接至 {self.auth.server_ip}:{self.auth.server_port}...", STATUS_COLOR_AUTHING)

    def _open_settings(self):
        dlg = SettingsDialog(
            self,
            self.auth.server_ip or "127.0.0.1",
            self.auth.server_port or 9000,
            self.auth.server_token,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            ip, port, token = dlg.get_values()
            if not ip or not token:
                QMessageBox.warning(self, "输入不完整", "请填写服务器 IP、端口和 Token。")
                return
            self.auth.update_server(ip, port)
            self.auth.server_token = token
            self.auth.token = token
            self.auth.save()
            self._set_status(f"🟠 连接中...", STATUS_COLOR_AUTHING)
            self._connect()

    def _connect(self):
        """Establish connection and authenticate."""
        if not self.auth.server_ip:
            return

        if self.conn:
            self.conn.disconnect()

        self.conn = Connection(self.auth.server_ip, self.auth.server_port)
        self.conn.on("_disconnect", lambda: self.sig_disconnected.emit())
        self.conn.on(PacketCommand.AUTH, self._handle_auth_response)
        self.conn.on(PacketCommand.ACK, self._handle_ack)
        self.conn.on(PacketCommand.NACK, self._handle_nack)
        self.conn.on("type_1", self._handle_text_message)
        self.conn.on("type_2", self._handle_file_packet)

        if self.conn.connect():
            self.sig_connected.emit()
            self.conn.send_auth(self.auth.token)
        else:
            self.sig_disconnected.emit()

    def _handle_auth_response(self, pkt: Packet):
        params = pkt.payload.get("params", {})
        if params.get("command") == PacketCommand.AUTH:
            self.sig_auth_ok.emit()

    def _handle_ack(self, pkt: Packet):
        cmd = pkt.payload.get("params", {}).get("command", "")
        log.info(f"Server ACK: {cmd}")

    def _handle_nack(self, pkt: Packet):
        reason = pkt.payload.get("params", {}).get("reason", "Unknown error")
        cmd = pkt.payload.get("params", {}).get("command", "")
        log.warning(f"Server NACK ({cmd}): {reason}")
        self.sig_auth_failed.emit(f"{cmd}: {reason}")

    def _handle_text_message(self, pkt: Packet):
        payload = pkt.payload
        self.sig_message.emit({
            "sender": payload.get("sender", "?"),
            "receiver": payload.get("receiver", "?"),
            "content": payload.get("content", ""),
            "ts": pkt.ts,
        })

    def _handle_file_packet(self, pkt: Packet):
        payload = pkt.payload
        action = payload.get("action")
        if action == FileAction.INIT:
            meta = payload.get("meta", {})
            self.sig_file_received.emit({
                "file_id": payload.get("file_id"),
                "name": meta.get("name"),
                "size": meta.get("size"),
                "hash": meta.get("hash"),
            })
        elif action == FileAction.COMPLETE:
            self.sig_transfer_complete.emit(payload.get("file_id", ""), True, "")
        elif action == FileAction.ERROR:
            self.sig_transfer_complete.emit(payload.get("file_id", ""), False, payload.get("reason", ""))

    # ─── Qt Slots ────────────────────────────────────────────────────

    def _on_connected(self):
        self._set_status(f"🟢 Online — {self.auth.server_ip}:{self.auth.server_port}", STATUS_COLOR_ONLINE)
        self._show_tray_notification("已连接", f"已连接至 {self.auth.server_ip}")

    def _on_disconnected(self):
        self._set_status("🔴 离线", STATUS_COLOR_OFFLINE)
        self._clear_reconnect()
        self._schedule_reconnect()
        self._show_tray_notification("连接已断开", "与服务器的连接已断开")

    def _on_auth_ok(self):
        self._set_status(f"🔵 已认证", STATUS_COLOR_ONLINE)
        self._show_tray_notification("认证成功", "身份验证通过！")

    def _on_auth_failed(self, reason: str):
        self._set_status("🔴 离线", STATUS_COLOR_OFFLINE)
        self._clear_reconnect()
        QMessageBox.warning(self, "认证失败", reason)

    def _on_message(self, data: dict):
        sender = data["sender"]
        content = data["content"]
        self._message_history.append(data)
        self._append_message(f"[{sender}]", content)
        self._show_tray_notification(f"来自 {sender}", content[:60])

    def _on_refresh_messages(self):
        self.msg_list.clear()
        for msg in self._message_history:
            sender = msg.get("sender", "?")
            content = msg.get("content", "")
            self._append_message(f"[{sender}]", content)

    def _on_file_received(self, data: dict):
        name = data["name"]
        size = data["size"]
        self._append_message("[SERVER]", f"📎 收到文件：{name}（{self._format_size(size)}）")
        self._show_tray_notification("收到文件", f"{name} ({self._format_size(size)})")

    def _on_transfer_complete(self, file_id: str, success: bool, message: str):
        if file_id in self._active_transfers:
            dlg = self._active_transfers.pop(file_id)
            if success:
                dlg.label.setText("传输完成！")
                dlg.progress.setValue(100)
                QTimer.singleShot(1500, dlg.accept)
            else:
                QMessageBox.warning(dlg, "传输失败", message)

    def _clear_reconnect(self):
        if self._reconnect_timer:
            self._reconnect_timer.stop()
            self._reconnect_timer.deleteLater()
            self._reconnect_timer = None

    def _schedule_reconnect(self):
        self._clear_reconnect()
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.timeout.connect(self._connect)
        self._reconnect_timer.setSingleShot(True)
        self._reconnect_timer.start(self.RECONNECT_DELAY * 1000)

    # ─── Message Sending ──────────────────────────────────────────────

    def _send_text(self):
        content = self.input_field.text().strip()
        if not content or not self.conn or not self.conn.is_connected():
            return
        if not self.auth.has_token():
            self._append_message("[系统]", "未认证，请点击设置连接服务器。")
            return

        try:
            self.conn.send_text(
                sender=self.auth.client_name,
                receiver="SERVER",
                content=content,
            )
            self.input_field.clear()
            msg_data = {"sender": self.auth.client_name, "content": content, "ts": ""}
            self._message_history.append(msg_data)
            self._append_message(f"[{self.auth.client_name}]", content)
        except Exception as e:
            self._append_message("[错误]", f"发送失败：{e}")

    def _attach_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择要发送的文件", "", "All files (*.*)")
        if not path:
            return
        size = os.path.getsize(path)
        if size > 100 * 1024 * 1024:
            QMessageBox.warning(self, "File Too Large", "Maximum file size is 100MB.")
            return

        self._upload_file(path)

    def _upload_file(self, path: str):
        filename = os.path.basename(path)
        size = os.path.getsize(path)

        file_id = uuid.uuid4().hex
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        file_hash = h.hexdigest()

        # Show progress dialog
        dlg = TransferDialog(self, filename, "Uploading")
        self._active_transfers[file_id] = dlg

        def progress(sent: int, total: int, speed: float):
            dlg.update_progress(sent, total, speed)

        def done(file_id: str, dlg_ref):
            self.sig_transfer_complete.emit(file_id, True, "")

        self._uploader = FileUploader(self.conn, progress_callback=progress)

        def worker():
            try:
                self._uploader.upload(path)
                # Wait for server COMPLETE packet
                def on_complete(pkt: Packet):
                    self._handle_file_packet(pkt)
                    done(file_id, dlg)
                self.conn.on(f"file_complete_{file_id}", on_complete)
            except Exception as e:
                self.sig_transfer_complete.emit(file_id, False, str(e))

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        dlg.exec()

    # ─── UI Helpers ───────────────────────────────────────────────────

    def _append_message(self, sender: str, content: str):
        item_text = f"{sender}\n{content}"
        self.msg_list.addItem(item_text)
        self.msg_list.scrollToBottom()

    def _set_status(self, text: str, color: str):
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color}; font-weight: bold;")

    def _show_tray_notification(self, title: str, body: str):
        self.tray.showMessage(title, body, QSystemTrayIcon.MessageIcon.Information, 3000)

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show()

    def _format_size(self, n: int) -> str:
        if n < 1024:
            return f"{n} B"
        elif n < 1024 * 1024:
            return f"{n/1024:.1f} KB"
        else:
            return f"{n/1024/1024:.1f} MB"

    # ─── Geometry Persistence ─────────────────────────────────────────

    def _restore_geometry(self):
        try:
            path = Path("nmsg_window_geom.json")
            if path.exists():
                data = json.loads(path.read_text())
                w, h = data.get("size", [700, 500])
                self.resize(w, h)
                if data.get("maximized"):
                    self.showMaximized()
        except Exception as e:
            log.warning(f"Failed to restore geometry: {e}")

    def _save_geometry(self):
        try:
            data = {
                "size": [self.width(), self.height()],
                "maximized": self.isMaximized(),
            }
            Path("nmsg_window_geom.json").write_text(json.dumps(data))
        except Exception:
            pass

    def closeEvent(self, event):
        self._save_geometry()
        if self.conn:
            self.conn.disconnect()
        event.accept()

    def _quit_app(self):
        self._save_geometry()
        if self.conn:
            self.conn.disconnect()
        QApplication.quit()
        sys.exit(0)


# ─── Application Entry Point ────────────────────────────────────────

from PyQt6.QtWidgets import QApplication


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    w = NmsgMainWindow()
    w.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
