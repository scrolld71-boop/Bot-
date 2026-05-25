import os
import json
import csv
import time
import asyncio
import logging
import shutil
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import FloodWait, UserNotParticipant

# ==========================================
# 1. CONFIGURATION
# ==========================================
OWNER_ID = 8570832903
BOT_TOKEN = "PUT_NEW_TOKEN_HERE"  # <--- MUST PASTE YOUR TOKEN FROM BOTFATHER HERE

# Telegram API Credentials (From your screenshot)
API_ID = 37869790
API_HASH = "4ef2895930191ca7896c9a6bba66f563"

# ==========================================
# 2. LOGGING SETUP
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("bot_logs.txt"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ==========================================
# 3. DATABASE MANAGEMENT (JSON/CSV)
# ==========================================
class LocalDB:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.files = {
            "users": "users.json",
            "admins": "admins.json",
            "channels": "channels.json",
            "settings": "settings.json",
            "banned": "banned.json",
            "stats": "stats.json",
            "schedule": "schedule.json"
        }
        self.csv_files = {"broadcast_logs": "broadcast_logs.csv"}
        self._init_files()

    def _init_files(self):
        # Auto-create missing JSON files
        for key, filepath in self.files.items():
            if not os.path.exists(filepath):
                with open(filepath, 'w') as f:
                    if key in ["users", "channels", "settings", "stats"]:
                        json.dump({}, f)
                    elif key in ["admins", "banned", "schedule"]:
                        json.dump([], f)
                        
        # Auto-create missing CSV files
        for key, filepath in self.csv_files.items():
            if not os.path.exists(filepath):
                with open(filepath, 'w', newline='') as f:
                    writer = csv.writer(f)
                    if key == "broadcast_logs":
                        writer.writerow(["Date", "Total_Users", "Success", "Failed", "Duration_Seconds"])

        # Populate Default Settings
        settings = self._read_sync("settings")
        if not settings:
            self._write_sync("settings", {
                "force_sub": None,
                "welcome_text": "Hello {mention}! Welcome to the bot.",
                "watermark": "",
                "auto_pin": False
            })

        # Populate Default Stats
        stats = self._read_sync("stats")
        if not stats:
            self._write_sync("stats", {"total_broadcasts": 0, "total_files_sent": 0})

    def _read_sync(self, key):
        with open(self.files[key], 'r') as f:
            return json.load(f)

    def _write_sync(self, key, data):
        with open(self.files[key], 'w') as f:
            json.dump(data, f, indent=4)

    async def read(self, key):
        async with self.lock:
            return self._read_sync(key)

    async def write(self, key, data):
        async with self.lock:
            self._write_sync(key, data)

    async def log_broadcast(self, total, success, failed, duration):
        async with self.lock:
            with open(self.csv_files["broadcast_logs"], 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), total, success, failed, round(duration, 2)])

db = LocalDB()

# ==========================================
# 4. UTILITY & STATE MANAGEMENT
# ==========================================
active_broadcasts = {}
rate_limit_cache = {}

async def is_admin(user_id: int) -> bool:
    if user_id == OWNER_ID:
        return True
    admins = await db.read("admins")
    return user_id in admins

async def is_banned(user_id: int) -> bool:
    banned = await db.read("banned")
    return user_id in banned

def generate_progress_bar(current, total, length=15):
    if total == 0: return "[░░░░░░░░░░░░░░░] 0%"
    percent = current / total
    filled = int(length * percent)
    bar = '█' * filled + '░' * (length - filled)
    return f"[{bar}] {round(percent * 100, 1)}%"

async def check_force_sub(client: Client, user_id: int) -> bool:
    settings = await db.read("settings")
    channel = settings.get("force_sub")
    if not channel:
        return True
    try:
        await client.get_chat_member(channel, user_id)
        return True
    except UserNotParticipant:
        return False
    except Exception:
        return True # Default to true if bot lacks permissions

# Custom Admin & Owner Filters
def admin_filter(_, __, message: Message):
    return bool(message.from_user and asyncio.run(is_admin(message.from_user.id)))

def owner_filter(_, __, message: Message):
    return bool(message.from_user and message.from_user.id == OWNER_ID)

is_admin_filter = filters.create(admin_filter)
is_owner_filter = filters.create(owner_filter)

# ==========================================
# 5. PYROGRAM CLIENT
# ==========================================
app = Client(
    "AutoBroadcastBot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# ==========================================
# 6. HANDLERS: USER & START
# ==========================================
@app.on_message(filters.command("start") & filters.private)
async def start_command(client: Client, message: Message):
    user_id = message.from_user.id
    
    # Rate Limiter
    now = time.time()
    if now - rate_limit_cache.get(user_id, 0) < 2:
        return
    rate_limit_cache[user_id] = now

    if await is_banned(user_id):
        return await message.reply("🚫 You are banned from using this bot.")

    # Force Sub Check
    if not await check_force_sub(client, user_id):
        settings = await db.read("settings")
        btn = [[InlineKeyboardButton("Join Channel", url=f"https://t.me/{settings['force_sub'].replace('@', '')}")]]
        return await message.reply("⚠️ **Please join our updates channel to use this bot.**", reply_markup=InlineKeyboardMarkup(btn))

    # User Tracking
    users = await db.read("users")
    if str(user_id) not in users:
        users[str(user_id)] = {
            "username": message.from_user.username,
            "join_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "active": True
        }
        await db.write("users", users)
        logger.info(f"New user joined: {user_id}")

    # Welcome Message
    settings = await db.read("settings")
    welcome_text = settings.get("welcome_text", "Hello {mention}!").replace("{mention}", message.from_user.mention)
    
    if await is_admin(user_id):
        welcome_text += "\n\n🛠 **Admin Menu Available.** Use /help to see commands."
        
    await message.reply(welcome_text)

# ==========================================
# 7. HANDLERS: ADMIN MANAGEMENT (OWNER ONLY)
# ==========================================
@app.on_message(filters.command("addadmin") & is_owner_filter)
async def add_admin(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply("Usage: `/addadmin user_id`")
    try:
        new_admin = int(message.command)
        admins = await db.read("admins")
        if new_admin not in admins:
            admins.append(new_admin)
            await db.write("admins", admins)
            await message.reply(f"✅ User `{new_admin}` is now an Admin.")
        else:
            await message.reply("User is already an admin.")
    except ValueError:
        await message.reply("Invalid User ID.")

@app.on_message(filters.command("removeadmin") & is_owner_filter)
async def remove_admin(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply("Usage: `/removeadmin user_id`")
    try:
        target = int(message.command)
        admins = await db.read("admins")
        if target in admins:
            admins.remove(target)
            await db.write("admins", admins)
            await message.reply(f"✅ Admin `{target}` removed.")
        else:
            await message.reply("User is not an admin.")
    except ValueError:
        await message.reply("Invalid User ID.")

@app.on_message(filters.command("admins") & is_admin_filter)
async def list_admins(client: Client, message: Message):
    admins = await db.read("admins")
    text = f"👑 **Owner:** `{OWNER_ID}`\n\n🛡 **Admins:**\n"
    for adm in admins:
        text += f"• `{adm}`\n"
    await message.reply(text)

# ==========================================
# 8. HANDLERS: CHANNEL MANAGEMENT
# ==========================================
@app.on_message(filters.command("addchannel") & is_admin_filter)
async def add_channel(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply("Usage: `/addchannel channel_id_or_username`")
    target = message.command
    try:
        chat = await client.get_chat(target)
        channels = await db.read("channels")
        channels[str(chat.id)] = {
            "title": chat.title,
            "auto_post": True,
            "mode": "copy"
        }
        await db.write("channels", channels)
        await message.reply(f"✅ Channel **{chat.title}** added successfully.")
    except Exception as e:
        await message.reply(f"❌ Error adding channel. Ensure I am an admin there.\nError: `{e}`")

@app.on_message(filters.command("removechannel") & is_admin_filter)
async def remove_channel(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply("Usage: `/removechannel channel_id`")
    target = message.command
    channels = await db.read("channels")
    if target in channels:
        del channels[target]
        await db.write("channels", channels)
        await message.reply("✅ Channel removed.")
    else:
        await message.reply("❌ Channel not found in database.")

@app.on_message(filters.command("channels") & is_admin_filter)
async def list_channels(client: Client, message: Message):
    channels = await db.read("channels")
    if not channels:
        return await message.reply("No channels linked.")
    text = "📢 **Linked Channels:**\n\n"
    for cid, data in channels.items():
        status = "✅ On" if data["auto_post"] else "❌ Off"
        text += f"• **{data['title']}** (`{cid}`)\n  Auto-Post: {status} | Mode: {data['mode']}\n\n"
    await message.reply(text)

# ==========================================
# 9. HANDLERS: BROADCAST SYSTEM
# ==========================================
@app.on_message(filters.command("broadcast") & is_admin_filter)
async def broadcast_command(client: Client, message: Message):
    if not message.reply_to_message:
        return await message.reply("⚠️ Please reply to the message you want to broadcast.")
    
    b_id = str(int(time.time()))
    active_broadcasts[b_id] = {"cancel": False}
    
    users = await db.read("users")
    total_users = len(users)
    
    cancel_btn = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Cancel Broadcast", callback_data=f"cancel_{b_id}")]])
    status_msg = await message.reply(f"⏳ **Initializing Broadcast...**\nTotal Target: `{total_users}` users", reply_markup=cancel_btn)
    
    success, failed = 0, 0
    start_time = time.time()
    
    for i, user_id_str in enumerate(users.keys()):
        if active_broadcasts[b_id]["cancel"]:
            break
            
        try:
            await message.reply_to_message.copy(int(user_id_str))
            success += 1
            await asyncio.sleep(0.05)
        except FloodWait as e:
            await asyncio.sleep(e.value + 1)
            await message.reply_to_message.copy(int(user_id_str))
            success += 1
        except Exception:
            failed += 1
            users[user_id_str]["active"] = False 

        if i % 20 == 0 and i > 0:
            elapsed = time.time() - start_time
            est_total = (elapsed / i) * total_users
            rem_time = round(est_total - elapsed)
            prog = generate_progress_bar(i, total_users)
            text = (f"📡 **Broadcasting in Progress...**\n\n"
                    f"{prog}\n"
                    f"✅ Success: `{success}` | ❌ Failed: `{failed}`\n"
                    f"⏳ Est. Time Left: `{rem_time}s`")
            try:
                await status_msg.edit(text, reply_markup=cancel_btn)
            except FloodWait as e:
                await asyncio.sleep(e.value)
            except:
                pass

    await db.write("users", users)
    duration = time.time() - start_time
    
    stats = await db.read("stats")
    stats["total_broadcasts"] += 1
    stats["total_files_sent"] += success
    await db.write("stats", stats)
    
    await db.log_broadcast(total_users, success, failed, duration)
    if b_id in active_broadcasts:
        del active_broadcasts[b_id]

    final_text = (f"✅ **Broadcast Finished!**\n\n"
                  f"⏱ Duration: `{round(duration, 2)}s`\n"
                  f"🎯 Success: `{success}`\n"
                  f"🚫 Failed: `{failed}`\n"
                  f"👥 Total Processed: `{success + failed}`")
    await status_msg.edit(final_text)

@app.on_callback_query(filters.regex(r"^cancel_") & is_admin_filter)
async def cancel_broadcast(client: Client, callback: CallbackQuery):
    b_id = callback.data.split("_")
    if b_id in active_broadcasts:
        active_broadcasts[b_id]["cancel"] = True
        await callback.answer("Broadcast stopping...", show_alert=True)
        await callback.message.edit_reply_markup(reply_markup=None)
    else:
        await callback.answer("Broadcast already ended.", show_alert=True)

# ==========================================
# 10. HANDLERS: AUTO-POSTING SYSTEM
# ==========================================
@app.on_message(filters.private & is_admin_filter & ~filters.command(["start", "help", "addadmin", "removeadmin", "admins", "addchannel", "removechannel", "channels", "broadcast", "users", "stats", "ban", "unban", "setwelcome", "setforce", "backup", "settings"]))
async def auto_post_handler(client: Client, message: Message):
    channels = await db.read("channels")
    settings = await db.read("settings")
    if not channels:
        return
        
    post_msg = await message.reply("⏳ Auto-posting to channels...")
    success, failed = 0, 0
    
    for cid, data in channels.items():
        if not data.get("auto_post", True):
            continue
        try:
            if data.get("mode", "copy") == "copy":
                caption = message.caption if message.caption else ""
                if settings.get("watermark"):
                    caption += f"\n\n{settings['watermark']}"
                sent = await message.copy(int(cid), caption=caption)
            else:
                sent = await message.forward(int(cid))
                
            if settings.get("auto_pin"):
                await sent.pin(disable_notification=True)
                
            success += 1
            await asyncio.sleep(0.5)
        except FloodWait as e:
            await asyncio.sleep(e.value + 1)
            await message.copy(int(cid))
            success += 1
        except Exception as e:
            logger.error(f"Auto-post failed for {cid}: {e}")
            failed += 1

    await post_msg.edit(f"✅ **Auto-Post Complete!**\n\n📢 Channels: `{success}`\n❌ Failed: `{failed}`")

# ==========================================
# 11. HANDLERS: USER & SECURITY SYSTEM
# ==========================================
@app.on_message(filters.command("users") & is_admin_filter)
async def get_users(client: Client, message: Message):
    users = await db.read("users")
    active = sum(1 for u in users.values() if u.get("active", True))
    await message.reply(f"👥 **User Database**\n\nTotal Users: `{len(users)}`\nActive Users: `{active}`")

@app.on_message(filters.command("ban") & is_admin_filter)
async def ban_user(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply("Usage: `/ban user_id`")
    try:
        uid = int(message.command)
        banned = await db.read("banned")
        if uid not in banned:
            banned.append(uid)
            await db.write("banned", banned)
            await message.reply(f"🚫 User `{uid}` banned.")
        else:
            await message.reply("User already banned.")
    except ValueError:
        await message.reply("Invalid ID.")

@app.on_message(filters.command("unban") & is_admin_filter)
async def unban_user(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply("Usage: `/unban user_id`")
    try:
        uid = int(message.command)
        banned = await db.read("banned")
        if uid in banned:
            banned.remove(uid)
            await db.write("banned", banned)
            await message.reply(f"✅ User `{uid}` unbanned.")
        else:
            await message.reply("User not banned.")
    except ValueError:
        await message.reply("Invalid ID.")

# ==========================================
# 12. HANDLERS: SETTINGS, BACKUP, HELP
# ==========================================
@app.on_message(filters.command("setforce") & is_admin_filter)
async def set_force(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply("Usage: `/setforce @channelusername` or channel ID")
    settings = await db.read("settings")
    settings["force_sub"] = message.command
    await db.write("settings", settings)
    await message.reply(f"✅ Force Sub channel set to: {message.command}")

@app.on_message(filters.command("backup") & is_admin_filter)
async def create_backup(client: Client, message: Message):
    msg = await message.reply("⏳ Creating backup archive...")
    backup_name = f"Backup_{datetime.now().strftime('%Y%m%d_%H%M')}.zip"
    
    import zipfile
    with zipfile.ZipFile(backup_name, 'w') as zipf:
        for f in db.files.values():
            if os.path.exists(f): zipf.write(f)
        for f in db.csv_files.values():
            if os.path.exists(f): zipf.write(f)
        if os.path.exists("bot_logs.txt"): zipf.write("bot_logs.txt")
            
    await message.reply_document(backup_name, caption="📦 **System Backup Complete**")
    os.remove(backup_name)
    await msg.delete()

@app.on_message(filters.command("stats") & is_admin_filter)
async def view_stats(client: Client, message: Message):
    stats = await db.read("stats")
    users = await db.read("users")
    channels = await db.read("channels")
    
    text = (f"📊 **Bot Analytics Dashboard**\n\n"
            f"👥 Total Users: `{len(users)}`\n"
            f"📢 Linked Channels: `{len(channels)}`\n"
            f"📡 Total Broadcasts: `{stats.get('total_broadcasts', 0)}`\n"
            f"📁 Files/Messages Sent: `{stats.get('total_files_sent', 0)}`\n")
    await message.reply(text)

@app.on_message(filters.command("help") & is_admin_filter)
async def help_menu(client: Client, message: Message):
    help_text = (
        "🛠 **Advanced Bot Dashboard**\n\n"
        "**Admin Management:**\n"
        "`/addadmin [id]` - Add admin (Owner)\n"
        "`/removeadmin [id]` - Remove admin\n"
        "`/admins` - List admins\n\n"
        "**Channel Management:**\n"
        "`/addchannel [id/@user]` - Link channel\n"
        "`/removechannel [id]` - Unlink channel\n"
        "`/channels` - List channels\n\n"
        "**Broadcast & Posts:**\n"
        "`/broadcast` - Reply to message to send to all users\n"
        "*Send any media to me directly to auto-post to linked channels.*\n\n"
        "**Users & Security:**\n"
        "`/users`, `/ban [id]`, `/unban [id]`\n"
        "`/setforce [channel]` - Enable force subscribe\n\n"
     
