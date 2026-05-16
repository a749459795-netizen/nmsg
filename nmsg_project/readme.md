# nmsg

A lightweight Windows-only C/S instant messaging and file transfer tool built with Python + PyQt6.

## Features

- **Token + IP dual-binding authentication** — permanent tokens, zero-trust validation
- **Three message types**: Text, File (streaming transfer), Packet (system commands)
- **SQLite** for structured data, filesystem for binary storage
- **PyQt6 GUI** with system tray notifications, transfer progress, and persistent session

## Project Structure

```
nmsg_project/
├── src/
│   ├── common/           # Shared: protocol, database, storage, exceptions
│   │   ├── protocol.py   # Packet envelope + Type 1/2/3 definitions
│   │   ├── database.py   # SQLite schema & queries
│   │   ├── storage.py    # IP-hierarchical file storage
│   │   └── exceptions.py
│   ├── server/           # Server implementation
│   │   ├── server.py     # Main TCP server loop
│   │   ├── server_gui.py # PyQt6 admin GUI
│   │   └── session.py    # Client session management
│   └── client/           # Client implementation
│       ├── gui.py        # PyQt6 main window
│       ├── connection.py # Socket connection + protocol
│       ├── auth.py       # Token persistence
│       └── file_transfer.py
├── server.py             # Server entry point
├── client.py             # Client entry point
├── requirements.txt
└── README.md
```

## Setup

```bash
pip install -r requirements.txt
```

## Running

**Server (CLI):**
```bash
python server.py --host 0.0.0.0 --port 9000
```

**Server (GUI):**
```bash
python server.py --gui
```

**Client:**
```bash
python client.py
```

## Protocol Overview

All packets are length-prefixed JSON (`4-byte big-endian length + JSON body`):

| Type | Description | Key Payload Fields |
|------|-------------|-------------------|
| 1 | Text | `sender`, `receiver`, `content` |
| 2 | File | `file_id`, `action` (INIT/PROGRESS/COMPLETE/ERROR), `meta` |
| 3 | Packet | `command` (AUTH/HEARTBEAT/REGISTER/...), `params` |

## Authentication Flow

1. New client → `REGISTER` command → receives permanent `token`
2. Returning client → `AUTH` command with stored `token`
3. Server validates `token + IP` binding on every request
4. Client sends `HEARTBEAT` every 60 seconds

## File Transfer

1. Client sends `Type 2 INIT` packet with `file_id`, `name`, `size`, `hash`
2. Client streams raw bytes directly over the TCP socket
3. Server hashes incoming stream, stores if hash matches
4. Server sends `COMPLETE` ACK or `ERROR` NACK

## Tech Stack

- Python 3.x
- PyQt6 (GUI)
- SQLite (database)
- TCP Socket (communication)