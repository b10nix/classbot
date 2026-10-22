<div align="center">

# 🟢 OMNI-NET TERMINAL `V4.5`
### Code name: **PLUMBER**

**Everything is a system. Everyone is a variable.**

*An asynchronous, role-aware Telegram platform for university class operations.*

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![Telegram](https://img.shields.io/badge/Telegram-Bot-26A5E4?logo=telegram&logoColor=white)
![Async](https://img.shields.io/badge/Architecture-Async-16A34A)
![Database](https://img.shields.io/badge/Database-MongoDB-47A248?logo=mongodb&logoColor=white)
![License](https://img.shields.io/badge/Purpose-Educational-64748B)

**Designed & maintained by Ben (The Architect)**

</div>

---

## `01` — Overview

**Omni-Net Terminal** is a modular Telegram bot for running multiple university class groups from one system. It brings academic tools, attendance, class finance, student records, community posts and moderation into a single interface.

The bot is built around asynchronous handlers, MongoDB-backed records and role-aware inline menus. Students see general modules; Major ECs get Major-scoped operations; Super Admin and Owner controls are restricted and checked again when protected pages are opened.

> The V4.5 label is used for this feature-and-maintenance release. The code name remains **Plumber**.

## `02` — System modules

### 🎓 Academic operations

- **Timetable** — view class schedules by Major and use supported Major shortcuts.
- **Class reminders** — scheduled reminders for configured Major groups.
- **Tasks & tutorials** — add, view and edit academic tasks, with due-date reminders.
- **Attendance** — open a session with a PIN, close it from its inline control and review attendance dates.
- **75% attendance calculator** — use `/skip` to estimate the remaining absence allowance.

Common commands:

```text
/timetable     /tt
/ceit          /ep          /ec
/archi         /mc          /mech          /civil
/tasks         /tutorials    /skip          /rollcall
```

### 💳 Finance & fund ledger

Major ECs can use the finance workspace to manage collections and record activity. Available command families include:

- Funds: `/newfund`, `/funds`, `/archive_fund`, `/unarchive_fund`
- Payments: `/pay`, `/undo`, `/history`, `/remind`
- Expenses and allocation: `/spend`, `/allocate`
- Summaries and exports: `/vault`, `/report`, `/fundstat`, `/ecstat`, `/export`, `/exportfund`, `/export_finance`, `/semhistory`

Finance access is permission-checked. Commands that need a Fund ID, roll number or amount accept those values as arguments or request them through the relevant flow.

### 🪪 Student registry

Student self-registration uses one guided flow rather than separate `/add` and `/ph` steps:

```text
/register
/myprofile
```

The registration flow gathers a roll number, Major selection when needed, English name, phone number and hostel status before confirmation. Major assignment is resolved from configured Major group membership; the flow should not guess a Major when it cannot resolve one.

EC tools include student lists and supported record maintenance commands such as `/list`, `/hostel`, `/setphone` and `/settg`.

### 💬 Community & identity

- `/confess` and `/confess1` — confession workflows.
- `/post` — submit a community post from the bot DM.
- `/nickname` — manage the supported nickname feature.
- **Replies** — respond through a published post's Reply button; the bot routes the reply to the related discussion destination.
- **Moderation** — eligible moderators can approve or reject pending content. Moderation actions are recorded in the configured log group.

Use the bot in a private chat for flows that ask questions or collect a submission. The exact steps shown by the bot are authoritative for each workflow.

### 🛠️ Utilities

| Area | Commands |
|---|---|
| General | `/weather`, `/wifi`, `/status`, `/creator` |
| Text / encoding | `/encrypt`, `/decrypt`, `/bin2hex`, `/hex2bin` |
| Group utilities | `/mention`, `/addcmd1`, `/addcmd2`, `/addcmd3`, `/delcmd` |
| Major / EC administration | `/setmajor`, `/setsemester`, `/setgroupmajor`, `/routes`, `/addec`, `/delec`, `/eclist` |

Some commands are restricted to group administrators, a registered Major EC, Super Admins or the Owner. A command appearing in this document does not bypass the permission checks enforced by the bot.

## `03` — Access model

| Role | Typical access |
|---|---|
| Student / public user | Academic lookup, social features, utilities and own profile |
| Major EC | Authorized Major's academic, student and finance operations |
| Super Admin | EC registry, Major/group routing and automation/data tools |
| Owner | Owner-only configuration and system administration |

Menu visibility is only a convenience layer. Protected callbacks and commands must still pass their authorization checks.

## `04` — Architecture

```text
Telegram users & class groups
            │
            ▼
  python-telegram-bot (async)
            │
   ┌────────┼─────────┐
   ▼        ▼         ▼
Academic  Community  Finance & Registry
handlers  handlers     handlers
   └────────┼─────────┘
            ▼
      Shared MongoDB
            │
            ▼
 Scheduled reminders / Excel exports
```

- **Runtime:** Python 3.11+
- **Telegram framework:** `python-telegram-bot` with async handlers and JobQueue
- **Database:** MongoDB via Motor (async driver)
- **Reports:** `openpyxl`
- **Deployment:** Linux VPS; PM2 can supervise the process
- **Structure:** modular handlers, inline callback menus, role-aware access and scheduled jobs

## `05` — Installation

### Requirements

- Python 3.11 or newer
- A Telegram bot token from BotFather
- A MongoDB URI
- A Linux VPS or another environment that can keep the bot process running
- PM2 is optional and used for process supervision in the example below

### Clone and install

```bash
git clone https://github.com/nobix666/classbot.git
cd classbot

python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### Configure environment

Create `.env` in the project root. Start from `.env.example` if it is present, and use the variable names below. Replace placeholders with your own values; do not commit real tokens, database URIs or private group IDs.

```dotenv
TELEGRAM_TOKEN=YOUR_TELEGRAM_BOT_TOKEN
MONGO_URI=YOUR_MONGODB_URI
OWNER_ID=YOUR_TELEGRAM_USER_ID
LOG_GROUP_ID=YOUR_LOG_GROUP_ID

# Shared community groups; comma-separated Telegram chat IDs
CLASS_GROUP_ID=-100111111111,-100222222222

# Major-specific groups; enter one or more IDs per configured Major
CEIT_MAJOR=
EP_MAJOR=
EC_MAJOR=
ARCHI_MAJOR=
MC_MAJOR=
MECH_MAJOR=
CIVIL_MAJOR=

WEATHER_API_KEY=YOUR_WEATHER_API_KEY
SUPER_ADMINS=123456789,987654321
VIP_USERS=
USE_MAJOR_DATABASES=false
MONGO_SHARED_DB_NAME=ClassBotDB
```

**Configuration notes**

- The project `.env.example` uses `TELEGRAM_TOKEN` (not `BOT_TOKEN`) and `SUPER_ADMINS` (not `SUPER_ADMIN`).
- All Majors use the shared database by default. Keep `USE_MAJOR_DATABASES=false` unless the application's database configuration is intentionally being changed.
- Use comma-separated Telegram IDs where a setting supports multiple values.
- The bot needs permission to access the configured groups and send messages where reminders, posts or logs are expected.

### Run directly

```bash
python main.py
```

### Run with PM2

With the virtual environment named `venv` in the project root:

```bash
pm2 start main.py --name plumber --interpreter ./venv/bin/python
pm2 save
pm2 status
pm2 logs plumber
```

## `06` — Release direction

V4.5 focuses on making the current system easier to operate and maintain:

- Clearer terminal-style `/start` menu and module labels
- Role-aware EC, Super Admin and Owner menu visibility
- Guided `/register` entry point in place of the old split registration instructions
- Documentation aligned with the current environment variable names and setup flow

The next development focus is maintainability, careful refactoring, performance and stronger automated tests rather than adding features for their own sake.

## `07` — Safety & maintenance

- Keep `.env` out of Git and never paste live bot tokens or database credentials into public issues.
- Rotate credentials immediately if they are exposed.
- Test permission-sensitive flows with student, EC, Super Admin and Owner accounts before deployment.
- Take a database backup before data cleanup, migration or reconciliation commands.
- Verify group IDs, permissions and environment values before enabling scheduled broadcast features.

## License

Built primarily for educational purposes and university class management. No separate open-source license is declared in this README; add a `LICENSE` file if a formal license is intended.

---

<div align="center">

**OMNI-NET TERMINAL V4.5 · PLUMBER**
*Everything is a system. Everyone is a variable.*

</div>
