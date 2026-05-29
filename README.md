# 🇰🇷 EPS Korea Monitor

Cek Status Pengiriman Kerja ke Korea Selatan — Web + Telegram Bot

## Features

- **Web Interface** — Dark theme, responsive, captcha protected
- **Telegram Bot** — Cek status langsung dari chat
- **Math Captcha** — Proteksi dari bot/scraper
- **Playwright Scraper** — Auto-login ke situs EPS Korea
- **Multi-account** — Cek satu atau banyak akun sekaligus

## Files

```
├── app.py              # FastAPI backend + Web UI
├── bot.py              # Telegram bot
├── eps_scraper.py      # Playwright scraper module
├── akun.txt            # Account list (format: id|password|birth)
├── requirements.txt    # Python dependencies
└── README.md
```

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. Run Web Interface

```bash
python app.py
# Access at http://localhost:8099
```

### 3. Run Telegram Bot

```bash
export EPS_BOT_TOKEN="your-token-from-botfather"
python bot.py
```

### 4. Nginx (Optional)

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location /eps/ {
        proxy_pass http://127.0.0.1:8099/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 120s;
    }

    location /eps/api/ {
        proxy_pass http://127.0.0.1:8099/api/;
        proxy_set_header Host $host;
        proxy_read_timeout 120s;
    }
}
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| GET | `/api/captcha` | Get math captcha |
| POST | `/api/check` | Check single account |
| POST | `/api/check/bulk` | Check multiple accounts |
| GET | `/api/check/file` | Check all from akun.txt |

## Account Format

```
NIK|Password|TanggalLahir(DDMMYY)
```

Example:
```
1234567890123|mypassword|020216
```

## Telegram Bot Commands

- `/start` — Welcome message
- `/set NIK Password TglLahir` — Save account
- `/check` — Check saved account
- `/help` — Help

Or send directly: `NIK|Password|TglLahir`

## Tech Stack

- **Backend:** Python, FastAPI, Playwright
- **Frontend:** HTML, CSS, JavaScript
- **Bot:** python-telegram-bot
- **Deploy:** Systemd services, nginx

## License

MIT
