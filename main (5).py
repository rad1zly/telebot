import os, json, sys, datetime, logging, gspread
from pathlib import Path
from threading import Thread
from flask import Flask
from oauth2client.service_account import ServiceAccountCredentials
from googleapiclient.discovery import build
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ───────── Config logging & keep‑alive (Replit) ─────────
logging.basicConfig(level=logging.INFO)
sys.stdout.reconfigure(encoding="utf-8")

app = Flask(__name__)


@app.route("/")
def home():
    return "Bot is alive."


def keep_alive():
    Thread(target=lambda: app.run(host="0.0.0.0", port=8080),
           daemon=True).start()


# ───────── ENVIRONMENT ─────────
BOT_TOKEN = "7873246699:AAE_mDr9H8YtLPy6gKI73o6RKDPtPwEqPH4"
TEMPLATE_ID = "1hlWmGBlgA-yO9lohi0pa1WKRujDYysiumVV5o01GFT0"  # spreadsheet template id

# ───────── Google credentials ─────────
SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]
creds = ServiceAccountCredentials.from_json_keyfile_name(
    "credentials.json", SCOPES)
gclient = gspread.authorize(creds)
drive_svc = build("drive", "v3", credentials=creds)

# ───────── User‑sheet mapping persisted locally ─────────
MAP_FILE = Path("user_sheets.json")
USER_SHEETS = json.loads(MAP_FILE.read_text()) if MAP_FILE.exists() else {}


def save_map():
    MAP_FILE.write_text(json.dumps(USER_SHEETS))


# ───────── Helpers ─────────
def copy_template(uid: int) -> str:
    body = {"name": f"Finance_{uid}"}
    newf = drive_svc.files().copy(fileId=TEMPLATE_ID, body=body).execute()
    sid = newf["id"]
    ss = gclient.open_by_key(sid)
    for w in ss.worksheets():
        if len(w.get_all_values()) > 1: w.resize(rows=1)
    USER_SHEETS[str(uid)] = sid
    save_map()
    logging.info(f"Template copied for {uid}: {sid}")
    return sid


def get_spreads(uid: int):
    sid = USER_SHEETS.get(str(uid)) or copy_template(uid)
    ss = gclient.open_by_key(sid)
    return ss.worksheet("Pemasukan"), ss.worksheet("Pengeluaran")


def current_month():
    return datetime.datetime.now().strftime("%B - %Y")


def row_for_month(ws, month):
    months = ws.col_values(1)
    if month in months: return months.index(month) + 1
    ws.append_row([month, 0, 0, 0, 0, 0])
    return len(months) + 1


def recalc_lainnya(ws, row):
    p, s, j, t = (int(ws.cell(row, i).value or 0) for i in (2, 3, 4, 5))
    ws.update_cell(row, 6, p - (s + j + t))


# ───────── Telegram states ─────────
CHOOSING_CAT, TYPING_AMT, TYPING_DESC, SETTING_IN, ADDING_IN, SETTING_BUD, CONFIRM_DELETE = range(
    7)


# ───────── Command /start ─────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    get_spreads(uid)  # ensure sheet
    await update.message.reply_text("👋 Selamat datang!\n"
                                    "/pemasukan – set pemasukan\n"
                                    "/tambah_pemasukan – tambah income\n"
                                    "/pengeluaran – catat belanja\n"
                                    "/set_budget – atur budget\n"
                                    "/cek – ringkasan\n"
                                    "/riwayat – 5 transaksi")


# ───────── Pemasukan handlers ─────────
async def pemasukan_start(u, c):
    await u.message.reply_text("Total pemasukan bulan ini:")
    return SETTING_IN


async def pemasukan_save(u, c):
    try:
        total = int(u.message.text.replace(',', '').replace('.', ''))
    except:
        await u.message.reply_text("⚠️ angka.")
        return SETTING_IN
    uid = u.effective_user.id
    ws_in, _ = get_spreads(uid)
    bulan = current_month()
    row = row_for_month(ws_in, bulan)
    ws_in.update_cell(row, 2, total)
    recalc_lainnya(ws_in, row)
    await u.message.reply_text("✅ disimpan.")
    return ConversationHandler.END


async def tambah_start(u, c):
    await u.message.reply_text("Nominal tambahan:")
    return ADDING_IN


async def tambah_save(u, c):
    try:
        add = int(u.message.text.replace(',', '').replace('.', ''))
    except:
        await u.message.reply_text("⚠️ angka.")
        return ADDING_IN
    uid = u.effective_user.id
    ws_in, _ = get_spreads(uid)
    bulan = current_month()
    row = row_for_month(ws_in, bulan)
    total = int(ws_in.cell(row, 2).value or 0) + add
    ws_in.update_cell(row, 2, total)
    recalc_lainnya(ws_in, row)
    await u.message.reply_text("✅ income ditambah.")
    return ConversationHandler.END


# ───────── Budget handlers ─────────
async def set_budget(u, c):
    c.user_data.clear()
    c.user_data['next'] = 'Savings'
    c.user_data['bud'] = {}
    await u.message.reply_text("Budget Savings:")
    return SETTING_BUD


async def simpan_budget(u, c):
    try:
        nom = int(u.message.text.replace(',', '').replace('.', ''))
    except:
        await u.message.reply_text("⚠️ angka.")
        return SETTING_BUD
    cat = c.user_data['next']
    c.user_data['bud'][cat] = nom
    if cat == 'Savings':
        c.user_data['next'] = 'Jajan'
        await u.message.reply_text("Budget Jajan:")
        return SETTING_BUD
    if cat == 'Jajan':
        c.user_data['next'] = 'Transport'
        await u.message.reply_text("Budget Transport:")
        return SETTING_BUD
    if cat == 'Transport':
        uid = u.effective_user.id
        ws_in, _ = get_spreads(uid)
        bulan = current_month()
        row = row_for_month(ws_in, bulan)
        ws_in.update_cell(row, 3, c.user_data['bud']['Savings'])
        ws_in.update_cell(row, 4, c.user_data['bud']['Jajan'])
        ws_in.update_cell(row, 5, c.user_data['bud']['Transport'])
        recalc_lainnya(ws_in, row)
        await u.message.reply_text("✅ Budget disimpan.")
        return ConversationHandler.END


# ───────── Pengeluaran handlers ─────────
async def out_start(u, c):
    kb = [["Savings"], ["Jajan"], ["Transport"], ["Lainnya"]]
    await u.message.reply_text("Pilih kategori:",
                               reply_markup=ReplyKeyboardMarkup(
                                   kb,
                                   one_time_keyboard=True,
                                   resize_keyboard=True))
    return CHOOSING_CAT


async def out_cat(u, c):
    c.user_data['kat'] = u.message.text
    await u.message.reply_text("Nominal:")
    return TYPING_AMT


async def out_nom(u, c):
    try:
        c.user_data['nom'] = int(
            u.message.text.replace(',', '').replace('.', ''))
    except:
        await u.message.reply_text("⚠️ angka.")
        return TYPING_AMT
    await u.message.reply_text("Deskripsi?")
    return TYPING_DESC


async def out_save(u, c):
    desc = u.message.text
    kat = c.user_data['kat']
    nom = c.user_data['nom']
    uid = u.effective_user.id
    ws_in, ws_out = get_spreads(uid)
    bulan = current_month()
    row = row_for_month(ws_in, bulan)
    # validasi
    headers = ws_in.row_values(1)
    budgets = ws_in.row_values(row)
    idx = headers.index(kat)
    bud = int(budgets[idx] or 0)
    real = 0
    for r in ws_out.get_all_values()[1:]:
        _, bln, kt, nominal, _ = r
        if bln == bulan and kt.strip() == kat: real += int(nominal)
    if real + nom > bud:
        sisa = bud - real
        await u.message.reply_text(f"⚠️ Budget {kat} tersisa Rp{sisa:,}.")
        return ConversationHandler.END
    tgl = datetime.datetime.now().strftime("%d/%m/%Y")
    ws_out.append_row([tgl, bulan, kat, nom, desc])
    await u.message.reply_text(f"✅ {kat}: Rp{nom:,} dicatat.")
    return ConversationHandler.END


# ───────── Ringkasan & Riwayat ─────────
async def cmd_cek(u, c):
    uid = u.effective_user.id
    ws_in, ws_out = get_spreads(uid)
    bulan = current_month()
    row = row_for_month(ws_in, bulan)
    headers = ws_in.row_values(1)
    values = ws_in.row_values(row)
    perkat = {h: 0 for h in headers[2:]}
    for r in ws_out.get_all_values()[1:]:
        _, bln, kt, nom, _ = r
        if bln == bulan: perkat[kt] += int(nom)
    peng_tot = sum(perkat.values())
    total = int(values[1] or 0)
    msg = f"📊 {bulan}\n\n"
    icons = {'Savings': '🏦', 'Jajan': '🍟', 'Transport': '🚌', 'Lainnya': '🧾'}
    for h, v in zip(headers[2:], values[2:]):
        v = int(v or 0)
        sisa = v - perkat[h]
        msg += f"{icons.get(h,'•')} {h}: Rp{sisa:,} (dari Rp{v:,})\n"
    msg += f"\n🧾 Total Pengeluaran: Rp{peng_tot:,}\n💡 Sisa Budget: Rp{total-peng_tot:,}"
    await u.message.reply_text(msg)


async def cmd_riwayat(u, c):
    uid = u.effective_user.id
    _, ws_out = get_spreads(uid)
    bulan = current_month()

    rows = ws_out.get_all_values()[1:]
    rows_bulan_ini = [r for r in rows if r[1] == bulan]

    if not rows_bulan_ini:
        await u.message.reply_text("📭 Belum ada transaksi bulan ini.")
        return

    msg = f"🧾 Riwayat Transaksi {bulan}:\n"
    for r in rows_bulan_ini:
        tgl, _, kat, nom, des = r
        msg += f"• {tgl} [{kat}] Rp{nom} — {des}\n"

    await u.message.reply_text(msg)



# ───── HAPUS TRANSAKSI ─────
async def cancel_transaksi(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    _, ws = get_spreads(uid)
    bulan = current_month()

    rows = [r for r in ws.get_all_values()[1:] if r[1] == bulan]
    if not rows:
        await update.message.reply_text("Tidak ada transaksi bulan ini.")
        return ConversationHandler.END

    ctx.user_data["del_rows"] = rows[-10:]  # tampilkan 10 terakhir
    msg = "🗑  Ketik nomor transaksi yang mau dihapus:\n"
    for i, r in enumerate(ctx.user_data["del_rows"], 1):
        msg += f"{i}. {r[0]} [{r[2]}] Rp{r[3]} – {r[4]}\n"
    await update.message.reply_text(msg)
    return CONFIRM_DELETE


async def confirm_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        idx = int(update.message.text.strip()) - 1
        target = ctx.user_data["del_rows"][idx]
    except (ValueError, IndexError, KeyError):
        await update.message.reply_text("Input salah.")
        return ConversationHandler.END

    uid = update.effective_user.id
    _, ws = get_spreads(uid)
    all_rows = ws.get_all_values()
    for i, r in enumerate(all_rows):
        if r == target:
            ws.delete_rows(i + 1)
            await update.message.reply_text("✅ Terhapus.")
            return ConversationHandler.END

    await update.message.reply_text("❌ Gagal, tidak ditemukan.")
    return ConversationHandler.END


# ───── RESET BULAN ─────
async def reset_all(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ws_in, ws_out = get_spreads(uid)
    bulan = current_month()

    # hapus baris bulan di Pemasukan
    for i, r in enumerate(ws_in.get_all_values()):
        if r and r[0] == bulan:
            ws_in.delete_rows(i + 1)
            break

    # hapus semua pengeluaran bulan
    offs = 0
    for i, r in enumerate(ws_out.get_all_values()):
        if r and r[1] == bulan:
            ws_out.delete_rows(i + 1 - offs)
            offs += 1

    await update.message.reply_text("♻️ Data bulan ini sudah di‑reset.")
    return ConversationHandler.END


# ───────── main ─────────
def main():
    keep_alive()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("cek", cmd_cek))
    app.add_handler(CommandHandler("riwayat", cmd_riwayat))
    app.add_handler(
        ConversationHandler(
            entry_points=[CommandHandler("pemasukan", pemasukan_start)],
            states={
                SETTING_IN: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND,
                                   pemasukan_save)
                ]
            },
            fallbacks=[]))
    app.add_handler(
        ConversationHandler(
            entry_points=[CommandHandler("tambah_pemasukan", tambah_start)],
            states={
                ADDING_IN:
                [MessageHandler(filters.TEXT & ~filters.COMMAND, tambah_save)]
            },
            fallbacks=[]))
    app.add_handler(
        ConversationHandler(
            entry_points=[CommandHandler("pengeluaran", out_start)],
            states={
                CHOOSING_CAT:
                [MessageHandler(filters.TEXT & ~filters.COMMAND, out_cat)],
                TYPING_AMT:
                [MessageHandler(filters.TEXT & ~filters.COMMAND, out_nom)],
                TYPING_DESC:
                [MessageHandler(filters.TEXT & ~filters.COMMAND, out_save)]
            },
            fallbacks=[]))
    app.add_handler(
        ConversationHandler(
            entry_points=[CommandHandler("set_budget", set_budget)],
            states={
                SETTING_BUD: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND,
                                   simpan_budget)
                ]
            },
            fallbacks=[]))
    # hapus transaksi
    app.add_handler(
        ConversationHandler(entry_points=[
            CommandHandler("cancel_transaksi", cancel_transaksi)
        ],
                            states={
                                CONFIRM_DELETE: [
                                    MessageHandler(
                                        filters.TEXT & ~filters.COMMAND,
                                        confirm_delete)
                                ]
                            },
                            fallbacks=[]))

    # reset bulan
    app.add_handler(CommandHandler("reset_all", reset_all))
    logging.info("Bot running")
    app.run_polling()
    
if __name__ == "__main__": main()
