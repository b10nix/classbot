# 🟢 OMNI-NET TERMINAL V4 (Code Name: Plumber)

> *"Everything is a system. Everyone is a variable."*
>
> **Designed & Maintained by Ben (The Architect)**

![Python](https://img.shields.io/badge/Python-3.11+-blue)
![Framework](https://img.shields.io/badge/python--telegram--bot-v22+-blueviolet)
![MongoDB](https://img.shields.io/badge/Database-MongoDB-success)
![Async](https://img.shields.io/badge/Architecture-Async-green)
![Status](https://img.shields.io/badge/Status-Online-brightgreen)

---

## 📖 About

**Omni-Net Terminal** is an asynchronous Telegram-based class management platform built to automate academic administration, class finance, and community interaction.

Originally developed to solve simple timetable problems, the project has gradually evolved into a complete ecosystem that manages multiple university class groups from a single platform.

Instead of flooding group chats with commands and repetitive reminders, Omni-Net handles everything automatically while keeping conversations clean through its built-in auto-delete system.

---

# ✨ Core Features

## 🎓 Academic Management

### 📅 Smart Timetable
- Instant timetable lookup (`/it`, `/ec`, etc.)
- Multi-major class merging
- Automatic class reminders before every class

### 📝 Tasks & Tutorials
- View assignments and tutorials
- Automatic deadline reminders
- Due-date tracking

### ✅ Smart Attendance
- Random 6-digit attendance PIN
- DM-based Roll Call
- Attendance report generation

### 📊 75% Attendance Calculator
Calculate exactly how many classes can still be skipped before falling below attendance requirements.

Command

```
/skip
```

---

# 💰 Finance Management

Designed for ECs and class treasurers.

## 💵 Fund Management

- Monthly fees
- Custom funds
- Active / Archived funds
- Fund allocation
- Expense tracking

## 💳 Payment System

- Record student payments
- Undo incorrect transactions
- Spending history
- Remaining balance calculation

## 📊 EC Dashboard

Every EC has a personalized dashboard displaying

- Assigned students
- Active collections
- Total collected
- Total spent
- Current balance

## 📄 Reports

Generate Excel reports instantly.

Features include

- Paid / Unpaid status
- Color-coded spreadsheets
- Automatic sorting
- Reminder messages for unpaid students

---

# 💬 Community Platform

## 🤫 Confession System

Students can anonymously express themselves through

```
/confess
/confess1
```

Supports

- Anonymous confession
- Custom signature
- Admin approval before publishing

---

## 💬 Community Posts

Students can create public posts directly from Bot DM.

```
/post
```

Features

- Multi-group broadcasting
- Permanent identity
- Admin approval
- Interactive replies

---

## 🎭 Identity System

Each user receives a permanent identity.

Supported identities

- Random Emoji
- Custom Nickname (one-time change)

Identity remains consistent across future posts.

---

## 💬 Reply System

Every published post includes a Reply button.

Replies are handled inside Bot DM and automatically return to the original group while keeping discussions organized.

---

# 🛡 Moderation System

## Admin Approval Workflow

Every confession and post enters an approval queue.

Admins can

- Approve
- Reject

simultaneously from multiple devices without conflicts.

---

## Moderation Logs

A dedicated log group records

- Sender information
- Content
- Target groups
- Approval history
- Moderator actions

---

# 🛠 Utilities

General utilities

- `/weather`
- `/wifi`
- `/status`
- `/creator`

Developer utilities

- `/encrypt`
- `/decrypt`
- `/bin2hex`
- `/hex2bin`

---

# ⚙ Architecture

Language

- Python 3.11+

Framework

- python-telegram-bot (Async)

Database

- MongoDB (Motor AsyncIO)

Spreadsheet

- openpyxl

Deployment

- Linux VPS
- PM2 Process Manager

Architecture Highlights

- Fully Asynchronous
- Modular Handler System
- Multi-group Support
- Interactive Inline UI
- Auto-delete Mechanism
- MongoDB-backed Storage

---

# 🚀 Installation

## Clone Repository

```bash
git clone https://github.com/nobix666/classbot.git

cd classbot
```

---

## Install Dependencies

```bash
pip install -r requirements.txt
```

---

## Configure Environment

Create

```
.env
```

Example

```env
BOT_TOKEN=

MONGO_URI=

OWNER_ID=

CLASS_GROUP_ID=-100111111111,-100222222222

LOG_GROUP_ID=-100333333333

VIP_USER=

SUPER_ADMIN=
```

Supports multiple class groups by separating IDs with commas.

---

## Run

```bash
pm2 start main.py \
--name plumber \
--interpreter ./venv/bin/python
```

```bash
pm2 save
```

---

# 💡 Highlights

- Interactive terminal interface (`/start`)
- Multi-group architecture
- Persistent identity system
- Admin approval workflow
- Automated finance management
- Attendance automation
- Excel reporting
- Reply-based community discussions
- Automatic cleanup to reduce chat clutter

---

# 🗺 Roadmap

Current focus is no longer adding major features.

Future development primarily focuses on

- Architecture refinement
- Code maintainability
- Performance optimization
- Internal refactoring
- Developer experience

---

# 📜 License

This project was built primarily for educational purposes and university class management.

---

Created and maintained by

**Ben (The Architect)**

*"Everything is a system. Everyone is a variable."*