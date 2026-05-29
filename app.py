"""FastAPI backend for EPS Korea monitoring."""
import json
import os
import io
import asyncio
import random
import string
import hashlib
import time
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel
from eps_scraper import get_eps_data, format_result

app = FastAPI(title="EPS Korea Monitor", version="1.1.0")

ACCOUNTS_FILE = Path(__file__).parent / "akun.txt"

# Simple in-memory captcha store: token -> {answer, created_at}
captcha_store = {}
CAPTCHA_TTL = 300  # 5 minutes


def generate_math_captcha() -> tuple[str, str, str]:
    """Generate a math captcha. Returns (token, question, answer)."""
    ops = ["+", "-"]
    op = random.choice(ops)
    
    if op == "+":
        a = random.randint(1, 50)
        b = random.randint(1, 50)
        answer = str(a + b)
    else:
        a = random.randint(10, 99)
        b = random.randint(1, a)  # Ensure non-negative result
        answer = str(a - b)
    
    question = f"{a} {op} {b} = ?"
    
    # Create token
    token = hashlib.sha256(f"{answer}:{time.time()}:{random.random()}".encode()).hexdigest()[:16]
    
    # Store answer
    captcha_store[token] = {
        "answer": answer,
        "created_at": time.time()
    }
    
    # Cleanup old entries
    now = time.time()
    expired = [k for k, v in captcha_store.items() if now - v["created_at"] > CAPTCHA_TTL]
    for k in expired:
        del captcha_store[k]
    
    return token, question, answer


def generate_captcha_image(text: str) -> bytes:
    """Generate a simple CAPTCHA image as SVG."""
    # Generate noise lines
    lines = ""
    for _ in range(4):
        x1 = random.randint(0, 180)
        y1 = random.randint(0, 50)
        x2 = random.randint(0, 180)
        y2 = random.randint(0, 50)
        color = f"#{random.randint(100,200):02x}{random.randint(100,200):02x}{random.randint(100,200):02x}"
        lines += f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="1.5"/>'

    # Generate noise dots
    dots = ""
    for _ in range(50):
        cx = random.randint(0, 180)
        cy = random.randint(0, 50)
        r = random.randint(1, 2)
        color = f"#{random.randint(100,200):02x}{random.randint(100,200):02x}{random.randint(100,200):02x}"
        dots += f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{color}"/>'

    # Generate text with random positions and rotations
    chars = ""
    for i, ch in enumerate(text):
        x = 15 + i * 30 + random.randint(-3, 3)
        y = 35 + random.randint(-5, 5)
        rotation = random.randint(-15, 15)
        color = f"#{random.randint(0,100):02x}{random.randint(0,100):02x}{random.randint(0,100):02x}"
        chars += f'<text x="{x}" y="{y}" font-size="28" font-family="monospace" font-weight="bold" fill="{color}" transform="rotate({rotation},{x},{y})">{ch}</text>'

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="180" height="50" viewBox="0 0 180 50">
  <rect width="180" height="50" fill="#1e293b" rx="8"/>
  {lines}
  {dots}
  {chars}
</svg>'''
    return svg.encode('utf-8')


def generate_captcha_token() -> tuple[str, str, str]:
    """Generate math captcha and return (token, question, svg_bytes)."""
    token, question, answer = generate_math_captcha()
    svg = generate_captcha_image(question)
    return token, question, svg


def verify_captcha(token: str, answer: str) -> bool:
    """Verify captcha answer."""
    if token not in captcha_store:
        return False

    stored = captcha_store[token]
    if time.time() - stored["created_at"] > CAPTCHA_TTL:
        del captcha_store[token]
        return False

    # Case-insensitive comparison
    if stored["answer"] == answer.strip().lower():
        del captcha_store[token]  # One-time use
        return True

    return False


class CheckRequest(BaseModel):
    username: str
    password: str
    birth: str
    captcha_token: str
    captcha_answer: str


class BulkCheckRequest(BaseModel):
    accounts: list[CheckRequest]


@app.get("/", response_class=HTMLResponse)
async def index():
    return """
<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>EPS Korea Monitor</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', system-ui, sans-serif; background: #0f172a; color: #e2e8f0; min-height: 100vh; }
        .container { max-width: 800px; margin: 0 auto; padding: 2rem; }
        h1 { text-align: center; font-size: 2rem; margin-bottom: 0.5rem; background: linear-gradient(135deg, #3b82f6, #8b5cf6); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
        .subtitle { text-align: center; color: #94a3b8; margin-bottom: 2rem; }
        .card { background: #1e293b; border-radius: 12px; padding: 1.5rem; margin-bottom: 1rem; border: 1px solid #334155; }
        .form-group { margin-bottom: 1rem; }
        label { display: block; color: #94a3b8; font-size: 0.875rem; margin-bottom: 0.25rem; }
        input { width: 100%; padding: 0.75rem; background: #0f172a; border: 1px solid #334155; border-radius: 8px; color: #e2e8f0; font-size: 1rem; }
        input:focus { outline: none; border-color: #3b82f6; }
        button { width: 100%; padding: 0.75rem; background: linear-gradient(135deg, #3b82f6, #8b5cf6); border: none; border-radius: 8px; color: white; font-size: 1rem; font-weight: 600; cursor: pointer; transition: opacity 0.2s; }
        button:hover { opacity: 0.9; }
        button:disabled { opacity: 0.5; cursor: not-allowed; }
        #result { white-space: pre-wrap; font-family: 'Courier New', monospace; font-size: 0.9rem; line-height: 1.6; color: #94a3b8; }
        .status { padding: 0.5rem 1rem; border-radius: 8px; margin-bottom: 1rem; text-align: center; }
        .status.loading { background: #1e3a5f; color: #60a5fa; }
        .status.success { background: #14532d; color: #4ade80; }
        .status.error { background: #7f1d1d; color: #f87171; }
        .captcha-group { display: flex; gap: 0.5rem; align-items: center; }
        .captcha-group input { flex: 1; }
        .captcha-refresh { background: none; border: 1px solid #334155; color: #94a3b8; padding: 0.5rem 0.75rem; border-radius: 8px; cursor: pointer; font-size: 1.2rem; width: auto; }
        .captcha-refresh:hover { background: #334155; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🇰🇷 EPS Korea Monitor</h1>
        <p class="subtitle">Cek Status Pengiriman Kerja ke Korea</p>
        
        <div class="card">
            <div class="form-group">
                <label>NIK / ID</label>
                <input type="text" id="username" placeholder="Masukkan NIK">
            </div>
            <div class="form-group">
                <label>Password</label>
                <input type="password" id="password" placeholder="Masukkan Password">
            </div>
            <div class="form-group">
                <label>Tanggal Lahir (DDMMYY)</label>
                <input type="text" id="birth" placeholder="Contoh: 020216" maxlength="6">
            </div>
            <div class="form-group">
                <label>Captcha</label>
                <div class="captcha-group">
                    <div id="captcha-question" style="background:#0f172a;padding:0.75rem;border-radius:8px;border:1px solid #334155;font-size:1.1rem;font-weight:bold;min-width:120px;text-align:center;">Loading...</div>
                    <input type="text" id="captcha-answer" placeholder="Jawaban" maxlength="4" autocomplete="off">
                    <button type="button" class="captcha-refresh" onclick="loadCaptcha()">↻</button>
                </div>
                <input type="hidden" id="captcha-token" value="">
            </div>
            <div id="status"></div>
            <button onclick="checkStatus()">🔍 Cek Status</button>
        </div>
        
        <div class="card">
            <h3 style="margin-bottom: 1rem; color: #94a3b8;">Hasil:</h3>
            <div id="result">Menunggu input...</div>
        </div>
    </div>

    <script>
    let captchaToken = '';
    
    async function loadCaptcha() {
        try {
            const resp = await fetch('/eps/api/captcha');
            const data = await resp.json();
            captchaToken = data.token;
            document.getElementById('captcha-token').value = data.token;
            document.getElementById('captcha-question').textContent = data.question;
            document.getElementById('captcha-answer').value = '';
        } catch(e) {
            console.error('Failed to load captcha:', e);
        }
    }
    
    // Load captcha on page load
    loadCaptcha();
    
    async function checkStatus() {
        const username = document.getElementById('username').value;
        const password = document.getElementById('password').value;
        const birth = document.getElementById('birth').value;
        const captchaAnswer = document.getElementById('captcha-answer').value;
        const captchaTokenVal = document.getElementById('captcha-token').value;
        const status = document.getElementById('status');
        const result = document.getElementById('result');
        
        if (!username || !password || !birth) {
            status.className = 'status error';
            status.textContent = '⚠️ Semua field wajib diisi';
            return;
        }
        
        if (!captchaAnswer) {
            status.className = 'status error';
            status.textContent = '⚠️ Masukkan captcha';
            return;
        }
        
        status.className = 'status loading';
        status.textContent = '⏳ Sedang mengecek...';
        result.textContent = '';
        
        try {
            const resp = await fetch('/eps/api/check', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    username, 
                    password, 
                    birth,
                    captcha_token: captchaTokenVal,
                    captcha_answer: captchaAnswer
                })
            });
            const data = await resp.json();
            
            if (data.error) {
                status.className = 'status error';
                status.textContent = '❌ ' + data.error;
                result.textContent = '';
                loadCaptcha(); // Refresh captcha on error
            } else {
                status.className = 'status success';
                status.textContent = '✅ Berhasil mengambil data';
                result.textContent = data.formatted;
                loadCaptcha(); // Refresh captcha after success
            }
        } catch(e) {
            status.className = 'status error';
            status.textContent = '❌ Gagal: ' + e.message;
            loadCaptcha();
        }
    }
    </script>
</body>
</html>
"""


@app.get("/api/captcha")
async def get_captcha():
    """Generate and return a new math captcha."""
    token, question, svg = generate_captcha_token()
    import base64
    image_b64 = base64.b64encode(svg).decode('utf-8')
    return {"token": token, "question": question, "image": image_b64}


@app.post("/api/check")
async def check_single(req: CheckRequest):
    """Check single account with captcha verification."""
    # Verify captcha first
    if not verify_captcha(req.captcha_token, req.captcha_answer):
        return {"error": "Captcha salah! Silakan coba lagi."}

    data = await get_eps_data(req.username, req.password, req.birth)
    if "error" in data:
        return {"error": data["error"]}
    formatted = format_result(data, req.username)
    return {"data": data, "formatted": formatted}


@app.post("/api/check/bulk")
async def check_bulk(req: BulkCheckRequest):
    """Check multiple accounts (first captcha verifies all)."""
    if not req.accounts:
        return {"error": "Tidak ada akun untuk dicek"}

    # Verify captcha from first account
    first = req.accounts[0]
    if not verify_captcha(first.captcha_token, first.captcha_answer):
        return {"error": "Captcha salah! Silakan coba lagi."}

    results = []
    for acc in req.accounts:
        data = await get_eps_data(acc.username, acc.password, acc.birth)
        if "error" in data:
            results.append({"username": acc.username, "error": data["error"]})
        else:
            formatted = format_result(data, acc.username)
            results.append({"username": acc.username, "data": data, "formatted": formatted})
    return {"results": results}


@app.get("/api/check/file")
async def check_from_file():
    """Check all accounts from akun.txt (no captcha for file-based)."""
    if not ACCOUNTS_FILE.exists():
        raise HTTPException(status_code=404, detail="akun.txt not found")

    lines = ACCOUNTS_FILE.read_text().strip().split("\n")
    accounts = []
    for line in lines:
        if "|" in line:
            parts = line.strip().split("|")
            if len(parts) == 3:
                accounts.append(CheckRequest(
                    username=parts[0],
                    password=parts[1],
                    birth=parts[2],
                    captcha_token="",
                    captcha_answer=""
                ))

    if not accounts:
        raise HTTPException(status_code=400, detail="No valid accounts in akun.txt (format: id|password|birth)")

    results = []
    for acc in accounts:
        data = await get_eps_data(acc.username, acc.password, acc.birth)
        if "error" in data:
            results.append({"username": acc.username, "error": data["error"]})
        else:
            formatted = format_result(data, acc.username)
            results.append({"username": acc.username, "data": data, "formatted": formatted})

    return {"count": len(results), "results": results}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "eps-monitor"}
