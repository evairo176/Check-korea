"""EPS Korea Scraper - Playwright-based scraper untuk Employment Permit System.

Versi 2 (label-based parsing). Versi lama memakai INDEX BARIS hardcode
(`rowsData[1]`, `[2]`, `[3]`) sehingga:

- `STATUS SLC` selalu dibaca dari baris ke-3 = "Keadaan pencarian pekerjaan"
  (bukan SLC sama sekali) -> hasil selalu "(Belum Ada SLC)" walau SLC sudah terbit.
- `mediasiInfo` selalu "0" karena filter case-sensitive `includes('Mediasi')`,
  padahal label halaman bahasa Indonesia = "Jumlah pencocokan/mediasi pekerjaan"
  (huruf kecil semua) -> nilai asli (mis. 1) tidak pernah kebaca.
- Baris progres lain (Pengeluaran Izin Kerja, Pengiriman/Penandatanganan SLC,
  CCVI, Tanggal Masuk, Penugasan Kerja) dan skor EPS-TOPIK tidak pernah dikirim.

Versi ini membaca SEMUA baris tabel progres apa adanya (tanpa asumsi urutan),
lalu memetakan label -> nama pendek, plus skor EPS-TOPIK dari halaman utama.
"""
import asyncio
import re

from playwright.async_api import async_playwright

LOGIN_URL = "https://www.eps.go.kr/eo/langMain.eo?langCD=in"
TARGET_URL = (
    "https://www.eps.go.kr/eo/EntProgCk.eo?pgID=P_000000015&langCD=in&menuID=10008"
)

# Label baris progres -> nama pendek yang enak dibaca (match substring, lowercase).
LABEL_SHORT: list[tuple[str, str]] = [
    ("ujian bahasa", "Ujian Bahasa Korea (EPS-TOPIK)"),
    ("pengiriman daftar pencari kerja", "Pengiriman Daftar Pencari Kerja"),
    ("penerimaan daftar pencari kerja", "Penerimaan Daftar Pencari Kerja"),
    ("keadaan pencarian pekerjaan", "Keadaan Pencarian Pekerjaan"),
    ("pengeluaran izin kerja", "Pengeluaran Izin Kerja"),
    ("pengiriman slc", "Pengiriman SLC"),
    ("penandatanganan slc", "Penandatanganan SLC"),
    ("pengeluaran ccvi", "Pengeluaran CCVI"),
    ("masuk sementara", "Tanggal Masuk Sementara"),
    ("masuk sesungguhnya", "Tanggal Masuk Sesungguhnya"),
    ("penugasan kerja", "Penugasan Kerja"),
    ("mediasi", "Jumlah Pencocokan/Mediasi Kerja"),
]

# Baca semua tabel halaman apa adanya: list of tables -> list of rows -> list of cell text.
# Nilai di tabel EPS kadang BUKAN teks di dalam <td>, tapi value dari <input>/<textarea>
# (innerText tidak membaca value) -> makanya di-fallback ke value form field.
DUMP_TABLES_JS = r'''() => {
    const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
    const NOISE = /^fileListSize/i;
    const cellText = (cell) => {
        const t = clean(cell.innerText);
        if (t) return t;
        return Array.from(cell.querySelectorAll('input, textarea, select'))
            .filter((e) => !NOISE.test(e.id || '') && !NOISE.test(e.name || ''))
            .map((e) => clean(e.value !== undefined && e.value !== null ? String(e.value) : (e.getAttribute('value') || '')))
            .filter((v) => v && v !== '0')
            .join(' ');
    };
    const tables = Array.from(document.querySelectorAll('table'));
    return tables.map((t) => Array.from(t.querySelectorAll('tr'))
        .map((tr) => Array.from(tr.querySelectorAll('td, th')).map(cellText))
        .filter((cells) => cells.length > 0));
}'''

# Skor EPS-TOPIK + status pulang sukarela ada di halaman utama setelah login.
LANDING_EXTRAS_JS = r'''() => {
    const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
    const txt = clean(document.body.innerText);
    const topik = txt.match(/EPS-?TOPIK[^0-9]{1,4}([0-9]{1,3}(?:\.[0-9]{1,2})?)/i);
    const vol = txt.match(/Pulang sukarela\s*([A-Za-z0-9_.\-]+)/i);
    return { topik: topik ? topik[1] : null, voluntaryReturn: vol ? vol[1] : null };
}'''

PENDING = {"", "-", "n/a", "nodata", "no data"}


def _short_label(label: str) -> str:
    low = label.lower()
    for key, short in LABEL_SHORT:
        if key in low:
            return short
    return label


def _is_pending(value: str) -> bool:
    return (value or "").strip().lower() in PENDING


def parse_tables(tables: list) -> dict:
    """Parse hasil dump tabel jadi struktur yang dipakai UI/format."""
    result: dict = {
        "name": None,
        "applicants": [],
        "milestones": [],
        "mediasi": None,
        "rowsData": [],
    }

    for rows in tables or []:
        header_idx = None
        for i, cells in enumerate(rows):
            joined = " ".join(cells).lower()
            low0 = (cells[0] or "").lower() if cells else ""
            # Nama pekerja (tabel identitas)
            if low0 in ("nama", "name") and len(cells) >= 2 and cells[1]:
                if not result["name"]:
                    result["name"] = cells[1]
            # Header tabel progres
            if "perkembangan" in joined or "progress" in joined:
                header_idx = i

        # Daftar pencari kerja: header memuat "NO." lalu baris angka
        has_no_header = any(
            (cells and (cells[0] or "").strip().upper().rstrip(".") == "NO")
            for cells in rows
        )
        if has_no_header:
            for cells in rows:
                no = (cells[0] or "").strip()
                if no.isdigit() and len(cells) >= 2:
                    received_raw = cells[2] if len(cells) > 2 else ""
                    validity = None
                    m = re.search(r"Masa Berlaku\s*:?\s*([0-9~./\-]+)", received_raw, re.I)
                    if m:
                        validity = m.group(1)
                    received = re.split(r"\*|Masa Berlaku", received_raw, flags=re.I)[0].strip()
                    result["applicants"].append(
                        {"no": no, "sent": cells[1].strip(), "received": received, "validity": validity}
                    )

        # Baris progres: SEMUA baris setelah header, tanpa asumsi urutan
        if header_idx is not None:
            for cells in rows[header_idx + 1:]:
                label = (cells[0] or "").strip()
                if not label:
                    continue
                status = (cells[1] if len(cells) > 1 else "").strip()
                date_cell = (cells[2] if len(cells) > 2 else "").strip()
                date = re.split(r"\*|Masa Berlaku", date_cell, flags=re.I)[0].strip()
                note = None
                m = re.search(r"(Masa Berlaku\s*:?\s*[0-9~./\-]+)", date_cell, re.I)
                if m:
                    note = m.group(1)
                entry = {
                    "label": label,
                    "labelShort": _short_label(label),
                    "status": status,
                    "date": date,
                    "note": note,
                    # "ada isinya" cukup dari salah satu kolom: status ATAU tanggal.
                    # (kalau dua-duanya kosong/- baru dianggap belum ada)
                    "done": not (_is_pending(status) and _is_pending(date)),
                }
                result["milestones"].append(entry)
                result["rowsData"].append([c for c in [label, status, date_cell] if c != ""])

                if "mediasi" in label.lower() or "mediation" in label.lower():
                    for cand in (status, date):
                        value = (cand or "").strip()
                        if value and not _is_pending(value):
                            result["mediasi"] = value
                            break
                    if result["mediasi"] is None:
                        result["mediasi"] = "0"

    if result["mediasi"] is None:
        result["mediasi"] = "0"

    # Backward-compat: `mediasiInfo` dipakai UI lama
    result["mediasiInfo"] = result["mediasi"]
    return result


async def get_eps_data(aid: str, apw: str, abirth: str) -> dict:
    """Ambil data EPS Korea untuk satu akun."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.goto(LOGIN_URL, timeout=60000)

            # ---- Login ----
            await page.wait_for_selector("#sKorTestNo", timeout=20000)
            await page.fill("#sKorTestNo", aid)
            await page.fill("#sFnrwRecvNo", apw)
            await page.click(".btn_login")

            # ---- Verifikasi tanggal lahir ----
            try:
                await page.wait_for_selector("#chkBirtDt", timeout=20000)
                await page.fill("#chkBirtDt", abirth)
                await page.click(".buttonE button")
            except Exception:
                await browser.close()
                return {"error": "VERIFIKASI GAGAL (CEK TGL LAHIR)"}

            await asyncio.sleep(4)

            # Skor EPS-TOPIK + pulang sukarela: ada di halaman utama sesudah login.
            extras: dict = {}
            try:
                extras = await page.evaluate(LANDING_EXTRAS_JS)
            except Exception:
                extras = {}

            # ---- Halaman kemajuan imigrasi ----
            await page.goto(TARGET_URL, wait_until="networkidle", timeout=60000)
            await asyncio.sleep(2)

            tables = await page.evaluate(DUMP_TABLES_JS)
            data = parse_tables(tables)

            body_text = (await page.inner_text("body"))[:4000]
            if not data["milestones"] and re.search(r"no\s*data", body_text, re.I):
                return {
                    "error": "NO DATA - data kemajuan belum tersedia di sistem EPS"
                }

            data["topikScore"] = extras.get("topik")
            data["voluntaryReturn"] = extras.get("voluntaryReturn")
            data["masaBerlaku"] = next(
                (a["validity"] for a in data["applicants"] if a.get("validity")), None
            )
            return data
        except Exception as e:
            return {"error": f"SISTEM ERROR: {str(e)[:50]}"}
        finally:
            await browser.close()


def format_result(data: dict, username: str) -> str:
    """Format hasil scraping jadi teks informatif (dipakai web UI + Telegram bot)."""
    if "error" in data:
        return f"❌ Akun: {username}\nError: {data['error']}"

    name = data.get("name") or "TIDAK DITEMUKAN"

    out = [f"📋 EPS Status - {name}", "━━━━━━━━━━━━━━━━━━━━━"]

    if data.get("topikScore"):
        out.append(f"🎯 Skor EPS-TOPIK: {data['topikScore']}")
    if data.get("voluntaryReturn"):
        out.append(f"🏠 Pulang Sukarela: {data['voluntaryReturn']}")

    applicants = data.get("applicants") or []
    if applicants:
        out.append("")
        out.append("📄 DAFTAR PENCARI KERJA")
        for a in applicants:
            out.append(f"   No. {a['no']} | Dikirim: {a.get('sent') or '-'} | Diterima: {a.get('received') or '-'}")
            if a.get("validity"):
                out.append(f"   Masa Berlaku: {a['validity']}")

    milestones = data.get("milestones") or []
    # Baris mediasi ditampilkan terpisah di bawah (biar gak dobel).
    milestones = [m for m in milestones if "mediasi" not in m["labelShort"].lower()]
    if milestones:
        out.append("")
        out.append("📊 PROGRES IMIGRASI")
        for m in milestones:
            detail = " ".join(
                x for x in (m.get("status"), m.get("date")) if x and not _is_pending(x)
            )
            icon = "✅" if m.get("done") else "⏳"
            out.append(f"{icon} {m['labelShort']}: {detail or 'belum ada'}")

    mediasi = data.get("mediasi", data.get("mediasiInfo", "0"))
    out.append("")
    out.append(f"🤝 Jumlah Pencocokan/Mediasi Kerja: {mediasi}")

    return "\n".join(out)
