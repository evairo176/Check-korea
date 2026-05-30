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


def generate_captcha_token() -> tuple[str, str]:
    """Generate math captcha and return (token, question)."""
    token, question, answer = generate_math_captcha()
    return token, question


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

    def validate_input(self) -> str | None:
        """Validate input fields. Returns error message or None."""
        import re
        if not self.username or len(self.username.strip()) < 3:
            return "NIK minimal 3 karakter"
        if not re.match(r'^[a-zA-Z0-9_-]+$', self.username.strip()):
            return "NIK mengandung karakter tidak valid"
        if not self.password or len(self.password) < 4:
            return "Password minimal 4 karakter"
        if not self.birth or not re.match(r'^[0-9]{6}$', self.birth.strip()):
            return "Format tanggal lahir harus 6 digit angka (DDMMYY)"
        dd = int(self.birth[0:2])
        mm = int(self.birth[2:4])
        if dd < 1 or dd > 31:
            return "Tanggal tidak valid (01-31)"
        if mm < 1 or mm > 12:
            return "Bulan tidak valid (01-12)"
        return None


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
        .field-hint { font-size: 0.75rem; color: #64748b; margin-top: 0.25rem; }
        .field-error { font-size: 0.75rem; color: #f87171; margin-top: 0.25rem; display: none; }
        input { width: 100%; padding: 0.75rem; background: #0f172a; border: 1px solid #334155; border-radius: 8px; color: #e2e8f0; font-size: 1rem; transition: border-color 0.2s; }
        input:focus { outline: none; border-color: #3b82f6; }
        input.invalid { border-color: #f87171; }
        input.valid { border-color: #22c55e; }
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
        .captcha-image { background: #1e293b; padding: 0.5rem; border-radius: 8px; border: 1px solid #334155; cursor: pointer; display: flex; align-items: center; justify-content: center; min-height: 50px; }
        .captcha-image:hover { border-color: #3b82f6; }
        .captcha-image svg { max-width: 100%; height: auto; }
        .captcha-refresh { background: none; border: 1px solid #334155; color: #94a3b8; padding: 0.5rem 0.75rem; border-radius: 8px; cursor: pointer; font-size: 1.2rem; width: auto; }
        .captcha-refresh:hover { background: #334155; }
        .input-icon { position: relative; }
        .input-icon .validation-icon { position: absolute; right: 12px; top: 50%; transform: translateY(-50%; font-size: 1.1rem; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🇰🇷 EPS Korea Monitor</h1>
        <p class="subtitle">Cek Status Pengiriman Kerja ke Korea</p>
        
        <div class="card">
            <div class="form-group">
                <label for="username">NIK / ID</label>
                <input type="text" id="username" placeholder="Masukkan NIK" autocomplete="off">
                <div class="field-hint">Nomor identitas yang terdaftar di EPS</div>
                <div class="field-error" id="username-error"></div>
            </div>
            <div class="form-group">
                <label for="password">Password</label>
                <input type="password" id="password" placeholder="Masukkan Password">
                <div class="field-error" id="password-error"></div>
            </div>
            <div class="form-group">
                <label for="birth">Tanggal Lahir (DDMMYY)</label>
                <input type="text" id="birth" placeholder="Contoh: 020290 (2 Feb 1990)" maxlength="6" pattern="[0-9]{6}" inputmode="numeric">
                <div class="field-hint">Format: 2 digit tanggal + 2 digit bulan + 2 digit tahun (DDMMYY)</div>
                <div class="field-error" id="birth-error"></div>
            </div>
            <div class="form-group">
                <label>Captcha</label>
                <div class="captcha-image" id="captcha-image" onclick="loadCaptcha()" title="Klik untuk refresh captcha">
                    <span style="color:#64748b">Loading captcha...</span>
                </div>
                <div class="captcha-group" style="margin-top: 0.5rem;">
                    <input type="text" id="captcha-answer" placeholder="Jawaban (contoh: 15)" maxlength="5" autocomplete="off" inputmode="numeric">
                    <button type="button" class="captcha-refresh" onclick="loadCaptcha()" title="Refresh captcha">↻</button>
                </div>
                <input type="hidden" id="captcha-token" value="">
                <div class="field-hint">Selesaikan soal matematika di atas</div>
                <div class="field-error" id="captcha-error"></div>
            </div>
            <div id="status"></div>
            <button onclick="checkStatus()" id="submit-btn">🔍 Cek Status</button>
        </div>
        
        <div class="card">
            <h3 style="margin-bottom: 1rem; color: #94a3b8;">Hasil:</h3>
            <div id="result">Menunggu input...</div>
        </div>
    </div>

    <script>
    let captchaToken = '';
    
    // Validation helpers
    function showError(fieldId, message) {
        const el = document.getElementById(fieldId + '-error');
        const input = document.getElementById(fieldId);
        if (el) { el.textContent = message; el.style.display = 'block'; }
        if (input) { input.classList.add('invalid'); input.classList.remove('valid'); }
    }
    
    function clearError(fieldId) {
        const el = document.getElementById(fieldId + '-error');
        const input = document.getElementById(fieldId);
        if (el) { el.textContent = ''; el.style.display = 'none'; }
        if (input) { input.classList.remove('invalid'); }
    }
    
    function markValid(fieldId) {
        const input = document.getElementById(fieldId);
        if (input) { input.classList.add('valid'); input.classList.remove('invalid'); }
    }
    
    function validateUsername(val) {
        if (!val) return 'NIK wajib diisi';
        if (val.length < 3) return 'NIK minimal 3 karakter';
        if (!/^[a-zA-Z0-9_-]+$/.test(val)) return 'NIK hanya boleh huruf, angka, - dan _';
        return null;
    }
    
    function validatePassword(val) {
        if (!val) return 'Password wajib diisi';
        if (val.length < 4) return 'Password minimal 4 karakter';
        return null;
    }
    
    function validateBirth(val) {
        if (!val) return 'Tanggal lahir wajib diisi';
        if (!/^[0-9]{6}$/.test(val)) return 'Format harus 6 digit angka (DDMMYY)';
        const dd = parseInt(val.substring(0, 2));
        const mm = parseInt(val.substring(2, 4));
        const yy = parseInt(val.substring(4, 6));
        if (dd < 1 || dd > 31) return 'Tanggal tidak valid (01-31)';
        if (mm < 1 || mm > 12) return 'Bulan tidak valid (01-12)';
        if (yy < 0 || yy > 99) return 'Tahun tidak valid (00-99)';
        return null;
    }
    
    function validateCaptcha(val) {
        if (!val) return 'Jawaban captcha wajib diisi';
        if (!/^[0-9]+$/.test(val)) return 'Jawaban harus berupa angka';
        return null;
    }
    
    // Real-time validation on blur
    document.getElementById('username').addEventListener('blur', function() {
        const err = validateUsername(this.value.trim());
        if (err) showError('username', err);
        else { clearError('username'); markValid('username'); }
    });
    
    document.getElementById('password').addEventListener('blur', function() {
        const err = validatePassword(this.value);
        if (err) showError('password', err);
        else { clearError('password'); markValid('password'); }
    });
    
    document.getElementById('birth').addEventListener('input', function() {
        // Only allow digits
        this.value = this.value.replace(/[^0-9]/g, '');
    });
    
    document.getElementById('birth').addEventListener('blur', function() {
        const err = validateBirth(this.value.trim());
        if (err) showError('birth', err);
        else { clearError('birth'); markValid('birth'); }
    });
    
    document.getElementById('captcha-answer').addEventListener('input', function() {
        // Only allow digits
        this.value = this.value.replace(/[^0-9]/g, '');
    });
    
    // Allow Enter key to submit
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Enter' && !document.getElementById('submit-btn').disabled) {
            checkStatus();
        }
    });
    
    async function loadCaptcha() {
        try {
            const resp = await fetch('/eps/api/captcha');
            const data = await resp.json();
            captchaToken = data.token;
            document.getElementById('captcha-token').value = data.token;
            
            // Show question as plain text
            const imgContainer = document.getElementById('captcha-image');
            imgContainer.innerHTML = '<span style="font-size:1.3rem;font-weight:bold;color:#e2e8f0;font-family:monospace">' + data.question + '</span>';
            
            document.getElementById('captcha-answer').value = '';
            clearError('captcha');
        } catch(e) {
            console.error('Failed to load captcha:', e);
            document.getElementById('captcha-image').innerHTML = '<span style="color:#f87171">Gagal load captcha. Klik untuk retry.</span>';
        }
    }
    
    // Load captcha on page load
    loadCaptcha();
    
    async function checkStatus() {
        const username = document.getElementById('username').value.trim();
        const password = document.getElementById('password').value;
        const birth = document.getElementById('birth').value.trim();
        const captchaAnswer = document.getElementById('captcha-answer').value.trim();
        const captchaTokenVal = document.getElementById('captcha-token').value;
        const status = document.getElementById('status');
        const result = document.getElementById('result');
        const submitBtn = document.getElementById('submit-btn');
        
        // Clear all previous errors
        ['username', 'password', 'birth', 'captcha'].forEach(clearError);
        
        // Validate all fields
        let hasError = false;
        
        const usernameErr = validateUsername(username);
        if (usernameErr) { showError('username', usernameErr); hasError = true; }
        
        const passwordErr = validatePassword(password);
        if (passwordErr) { showError('password', passwordErr); hasError = true; }
        
        const birthErr = validateBirth(birth);
        if (birthErr) { showError('birth', birthErr); hasError = true; }
        
        const captchaErr = validateCaptcha(captchaAnswer);
        if (captchaErr) { showError('captcha', captchaErr); hasError = true; }
        
        if (hasError) {
            status.className = 'status error';
            status.textContent = '⚠️ Mohon perbaiki input yang salah';
            return;
        }
        
        // Disable button during request
        submitBtn.disabled = true;
        status.className = 'status loading';
        status.textContent = '⏳ Sedang mengecek... (mungkin butuh 30-60 detik)';
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
                loadCaptcha();
            } else {
                status.className = 'status success';
                status.textContent = '✅ Berhasil mengambil data';
                result.textContent = data.formatted;
                loadCaptcha();
            }
        } catch(e) {
            status.className = 'status error';
            status.textContent = '❌ Gagal: ' + e.message;
            loadCaptcha();
        } finally {
            submitBtn.disabled = false;
        }
    }
    </script>
</body>
</html>
"""


@app.get("/api/captcha")
async def get_captcha():
    """Generate and return a new math captcha."""
    token, question = generate_captcha_token()
    return {"token": token, "question": question}


@app.post("/api/check")
async def check_single(req: CheckRequest):
    """Check single account with captcha verification."""
    # Validate inputs
    validation_error = req.validate_input()
    if validation_error:
        return {"error": validation_error}

    # Verify captcha
    if not verify_captcha(req.captcha_token, req.captcha_answer):
        return {"error": "Captcha salah! Silakan coba lagi."}

    data = await get_eps_data(req.username.strip(), req.password, req.birth.strip())
    if "error" in data:
        return {"error": data["error"]}
    formatted = format_result(data, req.username.strip())
    return {"data": data, "formatted": formatted}


@app.post("/api/check/bulk")
async def check_bulk(req: BulkCheckRequest):
    """Check multiple accounts (first captcha verifies all)."""
    if not req.accounts:
        return {"error": "Tidak ada akun untuk dicek"}

    # Validate all inputs first
    for i, acc in enumerate(req.accounts):
        err = acc.validate_input()
        if err:
            return {"error": f"Akun #{i+1}: {err}"}

    # Verify captcha from first account
    first = req.accounts[0]
    if not verify_captcha(first.captcha_token, first.captcha_answer):
        return {"error": "Captcha salah! Silakan coba lagi."}

    results = []
    for acc in req.accounts:
        data = await get_eps_data(acc.username.strip(), acc.password, acc.birth.strip())
        if "error" in data:
            results.append({"username": acc.username.strip(), "error": data["error"]})
        else:
            formatted = format_result(data, acc.username.strip())
            results.append({"username": acc.username.strip(), "data": data, "formatted": formatted})
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
