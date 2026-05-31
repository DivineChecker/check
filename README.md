# 🤖 New-API Daily Check-in Bot

A Telegram bot that auto check-ins to any New-API / One-API based site daily at **11:30 IST**.
Supports multiple sites, multiple accounts, cookie auth and username/password auth.

---

## Features

- ➕ Add unlimited sites via Telegram conversation
- 🍪 Cookie + API User auth **or** 🔑 Username + Password auth
- ✅ Cookie/login verification before saving
- ▶️ Manual "run now" trigger anytime
- 📋 List all sites with last check-in status
- 🗑 Remove sites
- ⏰ Automatic daily check-in at **11:30 IST**
- 💰 Shows balance + reward after each check-in
- 🔒 Owner-only (only your Telegram ID can use it)

---

## Setup

### Step 1 — Create a Telegram Bot

1. Open Telegram → search **@BotFather** → `/newbot`
2. Give it a name and username
3. Copy the **token** (looks like `7123456789:AAF...`)

### Step 2 — Get your Telegram User ID

1. Open Telegram → search **@userinfobot** → `/start`
2. It replies with your numeric ID (e.g. `987654321`)

### Step 3 — Deploy (choose one method)

---

#### 🐳 Option A — Docker (VPS / local, recommended for persistence)

**Requirements:** Docker + Docker Compose installed on your machine or VPS.

```bash
# 1. Clone / copy the project folder
cd checkin_bot

# 2. Create your .env file
cp .env.example .env
nano .env        # fill in BOT_TOKEN and OWNER_CHAT_ID

# 3. Build and start
docker compose up -d

# View logs
docker compose logs -f

# Stop
docker compose down

# Update after code change
docker compose up -d --build
```

The SQLite database is stored in a Docker **named volume** (`bot_data`) —
it persists across restarts, updates, and container rebuilds automatically.

**Useful commands:**
```bash
# Check bot status
docker compose ps

# Restart bot
docker compose restart checkin-bot

# Backup the DB
docker cp newapi-checkin-bot:/app/data/sites.db ./sites_backup.db

# Open a shell inside the container
docker compose exec checkin-bot bash
```

---

#### 🚂 Option B — Railway (free, no server needed)

1. Push this folder to a GitHub repo
2. Go to [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo**
3. Select your repo
4. Go to **Variables** tab and add:

   | Variable       | Value                        |
   |----------------|------------------------------|
   | `BOT_TOKEN`    | your bot token from BotFather |
   | `OWNER_CHAT_ID`| your Telegram numeric user ID |
   | `DB_PATH`      | `data/sites.db`              |

5. Railway will build and deploy automatically.

> **Persistence note:** Railway's free tier doesn't have persistent volumes.
> Your sites DB will reset on redeploy. Either upgrade to a paid plan with a volume,
> or use the bot to re-add sites after each deploy (takes 30 seconds).

### Step 3 (alternative) — Run locally

```bash
pip install -r requirements.txt
cp .env.example .env
# edit .env with your values
python main.py
```

---

## Bot Commands

| Command   | Description                        |
|-----------|------------------------------------|
| `/start`  | Open main menu                     |
| `/menu`   | Open main menu                     |
| `/add`    | Add a new site                     |
| `/list`   | List all saved sites               |
| `/run`    | Trigger check-in manually right now|
| `/cancel` | Cancel current operation           |

---

## Adding a Site

### Cookie Auth (recommended — more reliable)

1. Log into the site in your browser
2. Press **F12** → **Network** tab → filter **Fetch/XHR**
3. Reload the page or navigate the dashboard
4. Click any `/api/...` request → **Request Headers**
5. Copy the value after `Cookie: session=` → that's your **session cookie**
6. Also copy the value of `new-api-user:` → that's your **API User ID**

### Password Auth (easier to set up, re-logins each time)

Just provide your username/email and password.
The bot logs in fresh before each check-in, so you never need to update cookies.

---

## Refreshing a Cookie

Session cookies expire in ~1 month. When you see ❌ on verify:

1. Log in to the site in your browser
2. F12 → Network → any `/api/` request → copy new `session=` value
3. Remove the old site in the bot (`/menu` → 🗑 Remove)
4. Re-add it with the new cookie

Or switch to **Password auth** to avoid this entirely.

---

## File Structure

```
checkin_bot/
├── main.py          # Entry point (health server + bot)
├── bot.py           # Telegram bot handlers + scheduler
├── checkin.py       # Check-in & verification logic
├── db.py            # SQLite database layer
├── requirements.txt
├── Procfile         # Railway/Heroku process file
├── railway.toml     # Railway config
├── .env.example     # Environment variable template
└── .gitignore
```
