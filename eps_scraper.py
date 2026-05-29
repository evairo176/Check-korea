"""EPS Korea Scraper - Playwright-based scraper for employment permit system."""
import asyncio
import time
from playwright.async_api import async_playwright


async def get_eps_data(aid: str, apw: str, abirth: str) -> dict:
    """Scrape EPS Korea data for a single account."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.goto("https://www.eps.go.kr/eo/langMain.eo?langCD=in", timeout=60000)

            # Login
            await page.wait_for_selector('#sKorTestNo', timeout=20000)
            await page.fill('#sKorTestNo', aid)
            await page.fill('#sFnrwRecvNo', apw)
            await page.click('.btn_login')

            try:
                await page.wait_for_selector('#chkBirtDt', timeout=20000)
                await page.fill('#chkBirtDt', abirth)
                await page.click('.buttonE button')
            except Exception:
                await browser.close()
                return {"error": "VERIFIKASI GAGAL (CEK TGL LAHIR)"}

            await asyncio.sleep(4)

            target_url = "https://www.eps.go.kr/eo/EntProgCk.eo?pgID=P_000000015&langCD=in&menuID=10008"
            await page.goto(target_url, wait_until="networkidle", timeout=60000)

            data = await page.evaluate(r'''() => {
                const allCells = Array.from(document.querySelectorAll('td, th'));

                let name = "TIDAK DITEMUKAN";
                const nameIdx = allCells.findIndex(c => c.innerText.includes('Nama') || c.innerText.includes('Name'));
                if (nameIdx !== -1 && allCells[nameIdx + 1]) name = allCells[nameIdx + 1].innerText.trim();

                let mediasiInfo = "0";
                const medIdx = allCells.findIndex(c => c.innerText.includes('Mediasi') || c.innerText.includes('Mediation'));
                if (medIdx !== -1 && allCells[medIdx + 1]) {
                    mediasiInfo = allCells[medIdx + 1].innerText.trim();
                }

                const tables = Array.from(document.querySelectorAll('table'));
                const procTable = tables.find(t => t.innerText.includes('Perkembangan'));
                let rowsData = [];
                if (procTable) {
                    const trs = Array.from(procTable.querySelectorAll('tr'));
                    trs.forEach(tr => {
                        const tds = Array.from(tr.querySelectorAll('td')).map(td => td.innerText.trim());
                        if (tds.length >= 2) rowsData.push(tds);
                    });
                }

                return { name, mediasiInfo, rowsData };
            }''')

            return data
        except Exception as e:
            return {"error": f"SISTEM ERROR: {str(e)[:50]}"}
        finally:
            await browser.close()


def format_result(data: dict, username: str) -> str:
    """Format scraped data into readable text."""
    if "error" in data:
        return f"❌ Akun: {username}\nError: {data['error']}"

    name = data.get("name", "TIDAK DITEMUKAN")
    mediasi = data.get("mediasiInfo", "0")
    rows = data.get("rowsData", [])

    output = f"📋 **EPS Status — {name}**\n"
    output += f"━━━━━━━━━━━━━━━━━━━━━\n"

    labels = ["", "PENGIRIMAN", "PENERIMAAN", "STATUS SLC"]
    for idx in [1, 2, 3]:
        label = labels[idx] if idx < len(labels) else f"Kolom {idx}"
        if idx < len(rows) and len(rows[idx]) >= 2:
            status = rows[idx][1] if len(rows[idx]) > 1 else ""
            tanggal = rows[idx][2] if len(rows[idx]) > 2 else ""
            info = f"{status} {tanggal}".strip()
            if idx == 3:
                info = info if info else "(Belum Ada SLC)"
                output += f"📌 **{label}:** {info}\n"
                output += f"📌 **MEDIASI:** {mediasi}\n"
            else:
                output += f"📌 **{label}:** {info if info else '-'}\n"
        else:
            output += f"📌 **{label}:** -\n"

    return output
