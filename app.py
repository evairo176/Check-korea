"""FastAPI backend for EPS Korea monitoring."""
import os
import time
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel
from eps_scraper import get_eps_data, format_result

app = FastAPI(title="EPS Korea Monitor", version="1.2.0")

ACCOUNTS_FILE = Path(__file__).parent / "akun.txt"

# ── Rate limit per IP: pengganti proteksi lama. Set RATE_LIMIT_MAX=0 buat matiin ──
RATE_LIMIT_MAX = int(os.getenv("RATE_LIMIT_MAX", "30"))
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "300"))
rate_hits: dict[str, list[float]] = {}


def _client_ip(request: Request) -> str:
    for header in ("cf-connecting-ip", "x-real-ip", "x-forwarded-for"):
        value = request.headers.get(header)
        if value:
            return value.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_limit_retry(request: Request, cost: int = 1) -> int:
    """0 = boleh lanjut, >0 = detik harus tunggu."""
    if RATE_LIMIT_MAX <= 0:
        return 0
    now = time.time()
    ip = _client_ip(request)
    hits = [t for t in rate_hits.get(ip, []) if now - t < RATE_LIMIT_WINDOW]
    if len(hits) + cost > RATE_LIMIT_MAX:
        rate_hits[ip] = hits
        return int(RATE_LIMIT_WINDOW - (now - hits[0])) + 1 if hits else RATE_LIMIT_WINDOW
    hits.extend([now] * cost)
    rate_hits[ip] = hits
    if len(rate_hits) > 500:
        for key in [k for k, v in rate_hits.items() if all(now - t >= RATE_LIMIT_WINDOW for t in v)]:
            del rate_hits[key]
    return 0


def _rate_limit_payload(retry_after: int) -> dict:
    return {
        "error": f"Terlalu banyak permintaan. Coba lagi dalam {retry_after} detik.",
        "retryAfterSeconds": retry_after,
    }


class CheckRequest(BaseModel):
    username: str
    password: str
    birth: str

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
            return "Format tanggal lahir harus 6 digit angka (YYMMDD)"
        yy = int(self.birth[0:2])
        mm = int(self.birth[2:4])
        dd = int(self.birth[4:6])
        if mm < 1 or mm > 12:
            return "Bulan tidak valid (01-12)"
        if dd < 1 or dd > 31:
            return "Tanggal tidak valid (01-31)"
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
    <link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>KR</text></svg>">
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
        .btn-ghost { background: none; border: 1px solid #334155; color: #94a3b8; padding: 0.5rem 0.75rem; border-radius: 8px; cursor: pointer; font-size: 1.2rem; width: auto; }
        .btn-ghost:hover { background: #334155; }
        .input-icon { position: relative; }
        .input-icon .validation-icon { position: absolute; right: 12px; top: 50%; transform: translateY(-50%); font-size: 1.1rem; }
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
                <label for="birth">Tanggal Lahir (YYMMDD)</label>
                <input type="text" id="birth" placeholder="Contoh: 020216 (YY=02, MM=02, DD=16)" maxlength="6" pattern="[0-9]{6}" inputmode="numeric">
                <div class="field-hint">Format: 2 digit tahun + 2 digit bulan + 2 digit tanggal (YYMMDD)</div>
                <div class="field-error" id="birth-error"></div>
            </div>
            <div id="status"></div>
            <div class="form-group" style="display:flex; align-items:center; gap:0.5rem; margin-top:0.5rem;">
                <input type="checkbox" id="save-account" checked style="width:auto; margin:0;">
                <label for="save-account" style="margin:0; color:#94a3b8; font-size:0.85rem; cursor:pointer;">
                    💾 Simpan akun di browser (biar gak perlu input ulang)
                </label>
            </div>
            <button onclick="checkStatus()" id="submit-btn">🔍 Cek Status</button>
            <button type="button" id="clear-account-btn" onclick="clearSavedAccount()" class="btn-ghost" style="margin-left:0.5rem; font-size:0.85rem; width:auto;">🗑 Hapus akun tersimpan</button>
        </div>
        
        <div class="card">
            <h3 style="margin-bottom: 1rem; color: #94a3b8;">Hasil:</h3>
            <div id="result">Menunggu input...</div>
        </div>
    </div>

    <script>
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
        if (!/^[0-9]{6}$/.test(val)) return 'Format harus 6 digit angka (YYMMDD)';
        const yy = parseInt(val.substring(0, 2));
        const mm = parseInt(val.substring(2, 4));
        const dd = parseInt(val.substring(4, 6));
        if (mm < 1 || mm > 12) return 'Bulan tidak valid (01-12)';
        if (dd < 1 || dd > 31) return 'Tanggal tidak valid (01-31)';
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
    
    // Allow Enter key to submit
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Enter' && !document.getElementById('submit-btn').disabled) {
            checkStatus();
        }
    });
    
    // ═══ LocalStorage — simpan akun biar gak perlu input ulang ═══
    const STORAGE_KEY = 'eps_korea_account';
    const saveChk = () => document.getElementById('save-account');

    // Auto-fill dari localStorage saat halaman load
    function loadSavedAccount() {
        try {
            const raw = localStorage.getItem(STORAGE_KEY);
            if (!raw) { updateClearBtn(); return; }
            const acc = JSON.parse(raw);
            if (acc.username) document.getElementById('username').value = acc.username;
            if (acc.password) document.getElementById('password').value = acc.password;
            if (acc.birth) document.getElementById('birth').value = acc.birth;
            // Validasi nempel di atas
            ['username','password','birth'].forEach(id => {
                const err = document.getElementById(id)?.value.trim() ? null : 'x';
                if (!err) { clearError(id); markValid(id); }
            });
            if (acc.autoCheck) checkStatus();    // auto-cek sekali kalau mau
            updateClearBtn();
        } catch(e) { console.warn('Gagal baca akun tersimpan:', e); }
    }

    // Simpan (atau hapus) akun ke localStorage dipicu dari checkStatus
    function persistAccount() {
        const username = document.getElementById('username').value.trim();
        const password = document.getElementById('password').value;
        const birth = document.getElementById('birth').value.trim();
        try {
            if (saveChk() && saveChk().checked && username && password && birth) {
                localStorage.setItem(STORAGE_KEY, JSON.stringify({ username, password, birth, autoCheck:true }));
            } else {
                if (!(saveChk() && saveChk().checked)) localStorage.removeItem(STORAGE_KEY);
            }
        } catch(err) { console.warn('Gagal menyimpan akun:', err); }
        updateClearBtn();
    }

    function clearSavedAccount() {
        localStorage.removeItem(STORAGE_KEY);
        document.getElementById('username').value = '';
        document.getElementById('password').value = '';
        document.getElementById('birth').value = '';
        const st = document.getElementById('status');
        st.className = 'status';
        st.textContent = '🗑 Akun tersimpan dihapus.';
        updateClearBtn();
    }

    function updateClearBtn() {
        const has = !!localStorage.getItem(STORAGE_KEY);
        document.getElementById('clear-account-btn').style.display = has ? '' : 'none';
    }

    // Toggle simpan: hapus kalau di-uncheck
    document.addEventListener('change', function(e){
        if (e.target && e.target.id === 'save-account' && !e.target.checked) {
            localStorage.removeItem(STORAGE_KEY);
            updateClearBtn();
        }
    });

    loadSavedAccount();

    async function checkStatus() {
        const username = document.getElementById('username').value.trim();
        const password = document.getElementById('password').value;
        const birth = document.getElementById('birth').value.trim();
        const status = document.getElementById('status');
        const result = document.getElementById('result');
        const submitBtn = document.getElementById('submit-btn');
        
        // Clear all previous errors
        ['username', 'password', 'birth'].forEach(clearError);
        
        // Validate all fields
        let hasError = false;
        
        const usernameErr = validateUsername(username);
        if (usernameErr) { showError('username', usernameErr); hasError = true; }
        
        const passwordErr = validatePassword(password);
        if (passwordErr) { showError('password', passwordErr); hasError = true; }
        
        const birthErr = validateBirth(birth);
        if (birthErr) { showError('birth', birthErr); hasError = true; }
        
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
            const resp = await fetch('/api/check', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    username, 
                    password, 
                    birth
                })
            });
            const data = await resp.json();
            
            if (data.error) {
                status.className = 'status error';
                status.textContent = '❌ ' + data.error;
                result.textContent = '';
            } else {
                status.className = 'status success';
                status.textContent = '✅ Berhasil mengambil data';
                result.textContent = data.formatted;
            }
        } catch(e) {
            status.className = 'status error';
            status.textContent = '❌ Gagal: ' + e.message;
        } finally {
            persistAccount();    // simpan akun ke localStorage (atau hapus kalau di-uncheck)
            submitBtn.disabled = false;
        }
    }
    </script>
</body>
</html>
"""


@app.post("/api/check")
async def check_single(req: CheckRequest, request: Request):
    """Check single account."""
    # Validate inputs
    validation_error = req.validate_input()
    if validation_error:
        return {"error": validation_error}

    retry_after = _rate_limit_retry(request)
    if retry_after:
        return _rate_limit_payload(retry_after)

    data = await get_eps_data(req.username.strip(), req.password, req.birth.strip())
    if "error" in data:
        return {"error": data["error"]}
    formatted = format_result(data, req.username.strip())
    return {"data": data, "formatted": formatted}


@app.post("/api/check/bulk")
async def check_bulk(req: BulkCheckRequest, request: Request):
    """Check multiple accounts."""
    if not req.accounts:
        return {"error": "Tidak ada akun untuk dicek"}

    retry_after = _rate_limit_retry(request, cost=len(req.accounts))
    if retry_after:
        return _rate_limit_payload(retry_after)

    # Validate all inputs first
    for i, acc in enumerate(req.accounts):
        err = acc.validate_input()
        if err:
            return {"error": f"Akun #{i+1}: {err}"}

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
    """Check all accounts from akun.txt."""
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
