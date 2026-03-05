import sqlite3
import time
import threading
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from colorama import init, Fore, Style
import schedule
import requests
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Try to import plyer for desktop notifications
try:
    from plyer import notification
    NOTIFICATIONS_AVAILABLE = True
except ImportError:
    NOTIFICATIONS_AVAILABLE = False
    print(f"{Fore.YELLOW}⚠️  Warning: plyer not installed. Desktop notifications disabled.{Style.RESET_ALL}")
    print(f"{Fore.YELLOW}Install with: pip install plyer{Style.RESET_ALL}\n")

init()  # Colorama setup

# Database setup
DB_NAME = "notifications.db"

def init_db():
    """Initialize notifications database"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS notifications
                 (id INTEGER PRIMARY KEY, message TEXT, due_time TEXT, sent BOOLEAN DEFAULT 0)''')
    conn.commit()
    conn.close()

# ==================== TELEGRAM ====================
def get_telegram_config():
    """Get Telegram configuration from environment variables"""
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID')
    return bot_token, chat_id

def send_telegram_message(message):
    """Send a message via Telegram"""
    bot_token, chat_id = get_telegram_config()
    
    if not bot_token or not chat_id:
        print(f"{Fore.RED}❌ Telegram not configured.{Style.RESET_ALL}")
        return False
    
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message}
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            print(f"{Fore.GREEN}✅ Telegram message sent!{Style.RESET_ALL}")
            return True
        else:
            print(f"{Fore.RED}❌ Telegram API error: {response.status_code}{Style.RESET_ALL}")
            return False
    except requests.exceptions.RequestException as e:
        print(f"{Fore.RED}❌ Failed to send Telegram: {e}{Style.RESET_ALL}")
        return False

def verify_telegram_config():
    """Verify Telegram bot token and chat ID"""
    bot_token, chat_id = get_telegram_config()
    
    if not bot_token or not chat_id:
        print(f"{Fore.RED}❌ Telegram not configured!{Style.RESET_ALL}")
        return False
    
    print(f"{Fore.CYAN}ℹ️  Verifying Telegram...{Style.RESET_ALL}")
    url = f"https://api.telegram.org/bot{bot_token}/getMe"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            bot_info = response.json()
            if bot_info.get('ok'):
                bot_name = bot_info['result'].get('username', 'Unknown')
                print(f"{Fore.GREEN}✅ Bot valid! Username: @{bot_name}{Style.RESET_ALL}")
                return send_telegram_message("✅ Telegram verification successful!")
        print(f"{Fore.RED}❌ Bot token is invalid!{Style.RESET_ALL}")
        return False
    except requests.exceptions.RequestException as e:
        print(f"{Fore.RED}❌ Connection failed: {e}{Style.RESET_ALL}")
        return False

# ==================== DISCORD ====================
def get_discord_config():
    """Get Discord webhook URL from environment variables"""
    webhook_url = os.getenv('DISCORD_WEBHOOK_URL')
    return webhook_url

def send_discord_message(message):
    """Send a message via Discord webhook"""
    webhook_url = get_discord_config()
    
    if not webhook_url:
        print(f"{Fore.RED}❌ Discord not configured.{Style.RESET_ALL}")
        return False
    
    payload = {"content": message}
    
    try:
        response = requests.post(webhook_url, json=payload, timeout=10)
        if response.status_code == 204:
            print(f"{Fore.GREEN}✅ Discord message sent!{Style.RESET_ALL}")
            return True
        else:
            print(f"{Fore.RED}❌ Discord API error: {response.status_code}{Style.RESET_ALL}")
            return False
    except requests.exceptions.RequestException as e:
        print(f"{Fore.RED}❌ Failed to send Discord: {e}{Style.RESET_ALL}")
        return False

def verify_discord_config():
    """Verify Discord webhook"""
    webhook_url = get_discord_config()
    
    if not webhook_url:
        print(f"{Fore.RED}❌ Discord not configured!{Style.RESET_ALL}")
        return False
    
    print(f"{Fore.CYAN}ℹ️  Verifying Discord webhook...{Style.RESET_ALL}")
    return send_discord_message("✅ Discord verification successful!")

# ==================== PUSHOVER ====================
def get_pushover_config():
    """Get Pushover configuration from environment variables"""
    user_key = os.getenv('PUSHOVER_USER_KEY')
    api_token = os.getenv('PUSHOVER_API_TOKEN')
    return user_key, api_token

def send_pushover_message(message):
    """Send a message via Pushover"""
    user_key, api_token = get_pushover_config()
    
    if not user_key or not api_token:
        print(f"{Fore.RED}❌ Pushover not configured.{Style.RESET_ALL}")
        return False
    
    url = "https://api.pushover.net/1/messages.json"
    payload = {
        "token": api_token,
        "user": user_key,
        "message": message
    }
    
    try:
        response = requests.post(url, data=payload, timeout=10)
        if response.status_code == 200:
            print(f"{Fore.GREEN}✅ Pushover message sent!{Style.RESET_ALL}")
            return True
        else:
            print(f"{Fore.RED}❌ Pushover API error: {response.status_code}{Style.RESET_ALL}")
            return False
    except requests.exceptions.RequestException as e:
        print(f"{Fore.RED}❌ Failed to send Pushover: {e}{Style.RESET_ALL}")
        return False

def verify_pushover_config():
    """Verify Pushover configuration"""
    user_key, api_token = get_pushover_config()
    
    if not user_key or not api_token:
        print(f"{Fore.RED}❌ Pushover not configured!{Style.RESET_ALL}")
        return False
    
    print(f"{Fore.CYAN}ℹ️  Verifying Pushover...{Style.RESET_ALL}")
    return send_pushover_message("✅ Pushover verification successful!")

# ==================== EMAIL (GMAIL) ====================
def get_email_config():
    """Get Gmail configuration from environment variables"""
    smtp_server = os.getenv('EMAIL_SMTP_SERVER', 'smtp.gmail.com')
    smtp_port = int(os.getenv('EMAIL_SMTP_PORT', '587'))
    sender_email = os.getenv('EMAIL_SENDER')
    sender_password = os.getenv('EMAIL_PASSWORD')
    recipient_email = os.getenv('EMAIL_RECIPIENT')
    return smtp_server, smtp_port, sender_email, sender_password, recipient_email

def send_email_message(message):
    """Send a message via Gmail"""
    smtp_server, smtp_port, sender_email, sender_password, recipient_email = get_email_config()
    
    if not all([sender_email, sender_password, recipient_email]):
        print(f"{Fore.RED}❌ Gmail not configured.{Style.RESET_ALL}")
        return False
    
    try:
        msg = MIMEMultipart()
        if sender_email is None or recipient_email is None:
            print(f"{Fore.RED}❌ Email sender or recipient is not set!{Style.RESET_ALL}")
            return False
        msg['From'] = sender_email
        msg['To'] = recipient_email
        msg['Subject'] = "⏰ Notification Reminder"
        msg.attach(MIMEText(message, 'plain'))
        
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        if sender_email is None or sender_password is None:
            print(f"{Fore.RED}❌ Email sender or password is not set!{Style.RESET_ALL}")
            server.quit()
            return False
        server.login(sender_email, sender_password)
        server.send_message(msg)
        server.quit()
        
        print(f"{Fore.GREEN}✅ Email sent!{Style.RESET_ALL}")
        return True
    except Exception as e:
        print(f"{Fore.RED}❌ Failed to send email: {e}{Style.RESET_ALL}")
        return False

def verify_email_config():
    """Verify Gmail configuration"""
    smtp_server, smtp_port, sender_email, sender_password, recipient_email = get_email_config()
    
    if not all([sender_email, sender_password, recipient_email]):
        print(f"{Fore.RED}❌ Gmail not configured!{Style.RESET_ALL}")
        return False
    
    print(f"{Fore.CYAN}ℹ️  Verifying Gmail configuration...{Style.RESET_ALL}")
    print(f"{Fore.WHITE}SMTP Server: {smtp_server}:{smtp_port}{Style.RESET_ALL}")
    print(f"{Fore.WHITE}From: {sender_email}{Style.RESET_ALL}")
    print(f"{Fore.WHITE}To: {recipient_email}{Style.RESET_ALL}")
    return send_email_message("✅ Gmail verification successful!")

# ==================== NOTIFICATION MENUS ====================
def telegram_menu():
    """Telegram configuration and testing menu"""
    while True:
        print(f"\n{Fore.CYAN}╔═══════════════════════════════════════╗{Style.RESET_ALL}")
        print(f"{Fore.CYAN}║       📱 TELEGRAM SETTINGS            ║{Style.RESET_ALL}")
        print(f"{Fore.CYAN}╚═══════════════════════════════════════╝{Style.RESET_ALL}")
        
        bot_token, chat_id = get_telegram_config()
        status = f"{Fore.GREEN}✅ CONFIGURED{Style.RESET_ALL}" if bot_token and chat_id else f"{Fore.RED}❌ NOT CONFIGURED{Style.RESET_ALL}"
        print(f"{Fore.WHITE}Status: {status}{Style.RESET_ALL}\n")
        
        print(f"{Fore.WHITE}1. ✅ Verify Configuration{Style.RESET_ALL}")
        print(f"{Fore.WHITE}2. 📤 Send Test Message{Style.RESET_ALL}")
        print(f"{Fore.WHITE}3. 📋 Show .env Variables{Style.RESET_ALL}")
        print(f"{Fore.WHITE}4. ℹ️  Setup Instructions{Style.RESET_ALL}")
        print(f"{Fore.WHITE}0. ⬅️  Back{Style.RESET_ALL}")
        
        choice = input(f"\n{Fore.YELLOW}Choose: {Style.RESET_ALL}").strip()
        
        if choice == "1":
            verify_telegram_config()
        elif choice == "2":
            msg = input(f"{Fore.YELLOW}Test message: {Style.RESET_ALL}").strip() or "🧪 Test from Notification App!"
            send_telegram_message(msg)
        elif choice == "3":
            print(f"\n{Fore.GREEN}TELEGRAM_BOT_TOKEN=your_bot_token_here{Style.RESET_ALL}")
            print(f"{Fore.GREEN}TELEGRAM_CHAT_ID=your_chat_id_here{Style.RESET_ALL}")
        elif choice == "4":
            print(f"\n{Fore.CYAN}📚 Telegram Setup:{Style.RESET_ALL}")
            print(f"1. Message @BotFather on Telegram")
            print(f"2. Send /newbot and follow instructions")
            print(f"3. Get your bot token")
            print(f"4. Message @userinfobot to get your chat ID")
            print(f"5. Add both to .env file")
            input(f"\n{Fore.YELLOW}Press Enter...{Style.RESET_ALL}")
        elif choice == "0":
            break

def discord_menu():
    """Discord configuration and testing menu"""
    while True:
        print(f"\n{Fore.CYAN}╔═══════════════════════════════════════╗{Style.RESET_ALL}")
        print(f"{Fore.CYAN}║       💬 DISCORD SETTINGS             ║{Style.RESET_ALL}")
        print(f"{Fore.CYAN}╚═══════════════════════════════════════╝{Style.RESET_ALL}")
        
        webhook_url = get_discord_config()
        status = f"{Fore.GREEN}✅ CONFIGURED{Style.RESET_ALL}" if webhook_url else f"{Fore.RED}❌ NOT CONFIGURED{Style.RESET_ALL}"
        print(f"{Fore.WHITE}Status: {status}{Style.RESET_ALL}\n")
        
        print(f"{Fore.WHITE}1. ✅ Verify Configuration{Style.RESET_ALL}")
        print(f"{Fore.WHITE}2. 📤 Send Test Message{Style.RESET_ALL}")
        print(f"{Fore.WHITE}3. 📋 Show .env Variables{Style.RESET_ALL}")
        print(f"{Fore.WHITE}4. ℹ️  Setup Instructions{Style.RESET_ALL}")
        print(f"{Fore.WHITE}0. ⬅️  Back{Style.RESET_ALL}")
        
        choice = input(f"\n{Fore.YELLOW}Choose: {Style.RESET_ALL}").strip()
        
        if choice == "1":
            verify_discord_config()
        elif choice == "2":
            msg = input(f"{Fore.YELLOW}Test message: {Style.RESET_ALL}").strip() or "🧪 Test from Notification App!"
            send_discord_message(msg)
        elif choice == "3":
            print(f"\n{Fore.GREEN}DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...{Style.RESET_ALL}")
        elif choice == "4":
            print(f"\n{Fore.CYAN}📚 Discord Setup:{Style.RESET_ALL}")
            print(f"1. Go to your Discord server")
            print(f"2. Edit Channel → Integrations → Webhooks")
            print(f"3. Create New Webhook")
            print(f"4. Copy Webhook URL")
            print(f"5. Add DISCORD_WEBHOOK_URL to .env file")
            input(f"\n{Fore.YELLOW}Press Enter...{Style.RESET_ALL}")
        elif choice == "0":
            break

def pushover_menu():
    """Pushover configuration and testing menu"""
    while True:
        print(f"\n{Fore.CYAN}╔═══════════════════════════════════════╗{Style.RESET_ALL}")
        print(f"{Fore.CYAN}║       📲 PUSHOVER SETTINGS            ║{Style.RESET_ALL}")
        print(f"{Fore.CYAN}╚═══════════════════════════════════════╝{Style.RESET_ALL}")
        
        user_key, api_token = get_pushover_config()
        status = f"{Fore.GREEN}✅ CONFIGURED{Style.RESET_ALL}" if user_key and api_token else f"{Fore.RED}❌ NOT CONFIGURED{Style.RESET_ALL}"
        print(f"{Fore.WHITE}Status: {status}{Style.RESET_ALL}\n")
        
        print(f"{Fore.WHITE}1. ✅ Verify Configuration{Style.RESET_ALL}")
        print(f"{Fore.WHITE}2. 📤 Send Test Message{Style.RESET_ALL}")
        print(f"{Fore.WHITE}3. 📋 Show .env Variables{Style.RESET_ALL}")
        print(f"{Fore.WHITE}4. ℹ️  Setup Instructions{Style.RESET_ALL}")
        print(f"{Fore.WHITE}0. ⬅️  Back{Style.RESET_ALL}")
        
        choice = input(f"\n{Fore.YELLOW}Choose: {Style.RESET_ALL}").strip()
        
        if choice == "1":
            verify_pushover_config()
        elif choice == "2":
            msg = input(f"{Fore.YELLOW}Test message: {Style.RESET_ALL}").strip() or "🧪 Test from Notification App!"
            send_pushover_message(msg)
        elif choice == "3":
            print(f"\n{Fore.GREEN}PUSHOVER_USER_KEY=your_user_key_here{Style.RESET_ALL}")
            print(f"{Fore.GREEN}PUSHOVER_API_TOKEN=your_api_token_here{Style.RESET_ALL}")
        elif choice == "4":
            print(f"\n{Fore.CYAN}📚 Pushover Setup:{Style.RESET_ALL}")
            print(f"1. Go to https://pushover.net")
            print(f"2. Create an account")
            print(f"3. Get your User Key from dashboard")
            print(f"4. Create an Application/API Token")
            print(f"5. Add both to .env file")
            input(f"\n{Fore.YELLOW}Press Enter...{Style.RESET_ALL}")
        elif choice == "0":
            break

def email_menu():
    """Gmail configuration and testing menu"""
    while True:
        print(f"\n{Fore.CYAN}╔═══════════════════════════════════════╗{Style.RESET_ALL}")
        print(f"{Fore.CYAN}║       📧 GMAIL SETTINGS               ║{Style.RESET_ALL}")
        print(f"{Fore.CYAN}╚═══════════════════════════════════════╝{Style.RESET_ALL}")
        
        _, _, sender, _, recipient = get_email_config()
        status = f"{Fore.GREEN}✅ CONFIGURED{Style.RESET_ALL}" if sender and recipient else f"{Fore.RED}❌ NOT CONFIGURED{Style.RESET_ALL}"
        print(f"{Fore.WHITE}Status: {status}{Style.RESET_ALL}\n")
        
        print(f"{Fore.WHITE}1. ✅ Verify Configuration{Style.RESET_ALL}")
        print(f"{Fore.WHITE}2. 📤 Send Test Email{Style.RESET_ALL}")
        print(f"{Fore.WHITE}3. 📋 Show .env Variables{Style.RESET_ALL}")
        print(f"{Fore.WHITE}4. ℹ️  Setup Instructions{Style.RESET_ALL}")
        print(f"{Fore.WHITE}0. ⬅️  Back{Style.RESET_ALL}")
        
        choice = input(f"\n{Fore.YELLOW}Choose: {Style.RESET_ALL}").strip()
        
        if choice == "1":
            verify_email_config()
        elif choice == "2":
            msg = input(f"{Fore.YELLOW}Test message: {Style.RESET_ALL}").strip() or "🧪 Test from Notification App!"
            send_email_message(msg)
        elif choice == "3":
            print(f"\n{Fore.GREEN}EMAIL_SMTP_SERVER=smtp.gmail.com{Style.RESET_ALL}")
            print(f"{Fore.GREEN}EMAIL_SMTP_PORT=587{Style.RESET_ALL}")
            print(f"{Fore.GREEN}EMAIL_SENDER=your_email@gmail.com{Style.RESET_ALL}")
            print(f"{Fore.GREEN}EMAIL_PASSWORD=your_app_password{Style.RESET_ALL}")
            print(f"{Fore.GREEN}EMAIL_RECIPIENT=recipient@email.com{Style.RESET_ALL}")
        elif choice == "4":
            print(f"\n{Fore.CYAN}📚 Gmail Setup:{Style.RESET_ALL}")
            print(f"1. Go to Google Account → Security")
            print(f"2. Enable 2-Step Verification")
            print(f"3. Go to App Passwords")
            print(f"4. Generate an app password for 'Mail'")
            print(f"5. Use that password (not your regular password!)")
            print(f"6. Add all EMAIL_* variables to .env file")
            input(f"\n{Fore.YELLOW}Press Enter...{Style.RESET_ALL}")
        elif choice == "0":
            break

def notification_services_menu():
    """Main notification services menu"""
    while True:
        print(f"\n{Fore.CYAN}╔═══════════════════════════════════════╗{Style.RESET_ALL}")
        print(f"{Fore.CYAN}║    📬 NOTIFICATION SERVICES           ║{Style.RESET_ALL}")
        print(f"{Fore.CYAN}╚═══════════════════════════════════════╝{Style.RESET_ALL}")
        
        # Check status of each service
        tg_status = "✅" if all(get_telegram_config()) else "❌"
        dc_status = "✅" if get_discord_config() else "❌"
        po_status = "✅" if all(get_pushover_config()) else "❌"
        em_status = "✅" if all([get_email_config()[2], get_email_config()[4]]) else "❌"
        
        print(f"{Fore.WHITE}1. {tg_status} 📱 Telegram{Style.RESET_ALL}")
        print(f"{Fore.WHITE}2. {dc_status} 💬 Discord{Style.RESET_ALL}")
        print(f"{Fore.WHITE}3. {po_status} 📲 Pushover{Style.RESET_ALL}")
        print(f"{Fore.WHITE}4. {em_status} 📧 Gmail{Style.RESET_ALL}")
        print(f"{Fore.WHITE}5. 📋 Show Complete .env Example{Style.RESET_ALL}")
        print(f"{Fore.WHITE}0. ⬅️  Back to Main Menu{Style.RESET_ALL}")
        
        choice = input(f"\n{Fore.YELLOW}Choose: {Style.RESET_ALL}").strip()
        
        if choice == "1":
            telegram_menu()
        elif choice == "2":
            discord_menu()
        elif choice == "3":
            pushover_menu()
        elif choice == "4":
            email_menu()
        elif choice == "5":
            show_complete_env_example()
        elif choice == "0":
            break

def show_complete_env_example():
    """Show complete .env file example"""
    print(f"\n{Fore.CYAN}{'='*70}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}📄 COMPLETE .env FILE EXAMPLE{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{'='*70}{Style.RESET_ALL}\n")
    
    print(f"{Fore.YELLOW}# Telegram Configuration{Style.RESET_ALL}")
    print(f"{Fore.GREEN}TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz{Style.RESET_ALL}")
    print(f"{Fore.GREEN}TELEGRAM_CHAT_ID=987654321{Style.RESET_ALL}\n")
    
    print(f"{Fore.YELLOW}# Discord Configuration{Style.RESET_ALL}")
    print(f"{Fore.GREEN}DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/123456789/abcdef{Style.RESET_ALL}\n")
    
    print(f"{Fore.YELLOW}# Pushover Configuration{Style.RESET_ALL}")
    print(f"{Fore.GREEN}PUSHOVER_USER_KEY=your_user_key_here{Style.RESET_ALL}")
    print(f"{Fore.GREEN}PUSHOVER_API_TOKEN=your_api_token_here{Style.RESET_ALL}\n")
    
    print(f"{Fore.YELLOW}# Gmail Configuration{Style.RESET_ALL}")
    print(f"{Fore.GREEN}EMAIL_SMTP_SERVER=smtp.gmail.com{Style.RESET_ALL}")
    print(f"{Fore.GREEN}EMAIL_SMTP_PORT=587{Style.RESET_ALL}")
    print(f"{Fore.GREEN}EMAIL_SENDER=your_email@gmail.com{Style.RESET_ALL}")
    print(f"{Fore.GREEN}EMAIL_PASSWORD=your_app_password{Style.RESET_ALL}")
    print(f"{Fore.GREEN}EMAIL_RECIPIENT=recipient@email.com{Style.RESET_ALL}\n")
    
    print(f"{Fore.CYAN}{'='*70}{Style.RESET_ALL}")
    input(f"{Fore.YELLOW}Press Enter to continue...{Style.RESET_ALL}")

# ==================== NOTIFICATION CRUD ====================
def add_notification():
    print(f"{Fore.YELLOW}Enter message: {Style.RESET_ALL}", end="")
    msg = input().strip()
    if not msg:
        print(f"{Fore.RED}❌ Message cannot be empty!{Style.RESET_ALL}")
        return
    
    print(f"{Fore.YELLOW}Enter due time (e.g., '2025-10-08 14:00'): {Style.RESET_ALL}", end="")
    due = input().strip()
    
    try:
        datetime.strptime(due, "%Y-%m-%d %H:%M")
    except ValueError:
        try:
            datetime.strptime(due, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            print(f"{Fore.RED}❌ Invalid date format! Use YYYY-MM-DD HH:MM{Style.RESET_ALL}")
            return
    
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT INTO notifications (message, due_time) VALUES (?, ?)", (msg, due))
    notification_id = c.lastrowid
    conn.commit()
    conn.close()
    print(f"{Fore.GREEN}✅ Added! ID: {notification_id}{Style.RESET_ALL}")

def view_notifications():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT * FROM notifications ORDER BY due_time")
    rows = c.fetchall()
    conn.close()
    
    if not rows:
        print(f"{Fore.YELLOW}⚠️  No notifications.{Style.RESET_ALL}")
        return
    
    print(f"\n{Fore.CYAN}{'='*70}{Style.RESET_ALL}")
    for row in rows:
        status = f"{Fore.GREEN}✅ SENT{Style.RESET_ALL}" if row[3] else f"{Fore.YELLOW}⏳ PENDING{Style.RESET_ALL}"
        print(f"{Fore.WHITE}ID: {row[0]} | Msg: {row[1]}{Style.RESET_ALL}")
        print(f"{Fore.WHITE}Due: {row[2]} | Status: {status}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}{'-'*70}{Style.RESET_ALL}")

def delete_notification():
    print(f"{Fore.YELLOW}Enter notification ID to delete: {Style.RESET_ALL}", end="")
    notif_id = input().strip()
    
    if not notif_id.isdigit():
        print(f"{Fore.RED}❌ Invalid ID!{Style.RESET_ALL}")
        return
    
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT * FROM notifications WHERE id = ?", (notif_id,))
    if not c.fetchone():
        print(f"{Fore.RED}❌ Notification ID {notif_id} not found!{Style.RESET_ALL}")
        conn.close()
        return
    
    c.execute("DELETE FROM notifications WHERE id = ?", (notif_id,))
    conn.commit()
    conn.close()
    print(f"{Fore.GREEN}✅ Deleted notification ID {notif_id}!{Style.RESET_ALL}")

def edit_notification():
    print(f"{Fore.YELLOW}Enter notification ID to edit: {Style.RESET_ALL}", end="")
    notif_id = input().strip()
    
    if not notif_id.isdigit():
        print(f"{Fore.RED}❌ Invalid ID!{Style.RESET_ALL}")
        return
    
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT * FROM notifications WHERE id = ?", (notif_id,))
    row = c.fetchone()
    
    if not row:
        print(f"{Fore.RED}❌ Notification ID {notif_id} not found!{Style.RESET_ALL}")
        conn.close()
        return
    
    print(f"{Fore.CYAN}Current message: {row[1]}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Current due time: {row[2]}{Style.RESET_ALL}")
    
    print(f"{Fore.YELLOW}Enter new message (press Enter to keep): {Style.RESET_ALL}", end="")
    new_msg = input().strip() or row[1]
    
    print(f"{Fore.YELLOW}Enter new due time (press Enter to keep): {Style.RESET_ALL}", end="")
    new_due = input().strip() or row[2]
    
    if new_due != row[2]:
        try:
            datetime.strptime(new_due, "%Y-%m-%d %H:%M")
        except ValueError:
            try:
                datetime.strptime(new_due, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                print(f"{Fore.RED}❌ Invalid format! Keeping original.{Style.RESET_ALL}")
                new_due = row[2]
    
    c.execute("UPDATE notifications SET message = ?, due_time = ? WHERE id = ?", (new_msg, new_due, notif_id))
    conn.commit()
    conn.close()
    print(f"{Fore.GREEN}✅ Updated notification ID {notif_id}!{Style.RESET_ALL}")

def send_notifications():
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT * FROM notifications WHERE due_time <= ? AND sent = 0", (now,))
    pending = c.fetchall()
    
    if not pending:
        print(f"{Fore.YELLOW}⚠️  No pending notifications to send.{Style.RESET_ALL}")
        conn.close()
        return
    
    for row in pending:
        msg = row[1]
        print(f"{Fore.GREEN}📢 Sending: {msg}{Style.RESET_ALL}")
        
        # Send desktop notification if available
        if NOTIFICATIONS_AVAILABLE and notification is not None and callable(getattr(notification, "notify", None)):
            try:
                notify_func = getattr(notification, "notify", None)
                if callable(notify_func):
                    notify_func(
                        title="⏰ Reminder!",
                        message=msg,
                        timeout=10
                    )
                else:
                    print(f"{Fore.YELLOW}⚠️  plyer is installed but notification.notify is not callable. Desktop notifications skipped.{Style.RESET_ALL}")
            except Exception as e:
                print(f"{Fore.RED}❌ Desktop notification failed: {e}{Style.RESET_ALL}")
        elif NOTIFICATIONS_AVAILABLE:
            print(f"{Fore.YELLOW}⚠️  plyer is installed but notification.notify is not callable. Desktop notifications skipped.{Style.RESET_ALL}")
        else:
            print(f"{Fore.YELLOW}⚠️  Desktop notifications are not available.{Style.RESET_ALL}")
        
        # Send to all configured services
        send_telegram_message(f"⏰ Reminder: {msg}")
        send_discord_message(f"⏰ Reminder: {msg}")
        send_pushover_message(f"⏰ Reminder: {msg}")
        send_email_message(f"⏰ Reminder: {msg}")
        
        # Mark as sent
        c.execute("UPDATE notifications SET sent = 1 WHERE id = ?", (row[0],))
    
    conn.commit()
    conn.close()
    print(f"{Fore.GREEN}✅ Processed {len(pending)} notification(s).{Style.RESET_ALL}")

def background_runner():
    """Runs in a separate thread to check notifications every minute"""
    print(f"{Fore.CYAN}🔄 Background scheduler started. Checking every minute...{Style.RESET_ALL}")
    while True:
        schedule.run_pending()
        time.sleep(60)

def main():
    init_db()
    
    # Schedule notification check every minute
    try:
        schedule.every(1).minutes.do(send_notifications)
    except Exception as e:
        print(f"{Fore.RED}⚠️  Warning: Could not set up scheduler: {e}{Style.RESET_ALL}")
        print(f"{Fore.YELLOW}Continuing without automatic background checks...{Style.RESET_ALL}")
    
    # Start background thread
    scheduler_thread = threading.Thread(target=background_runner, daemon=True)
    scheduler_thread.start()
    
    print(f"{Fore.CYAN}{'='*70}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}🚨 Notification App v2.0.0 Started!{Style.RESET_ALL}")
    print(f"{Fore.CYAN}🔄 Background scheduler is running...{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{'='*70}{Style.RESET_ALL}\n")
    
    while True:
        print(f"\n{Fore.WHITE}╔═══════════════════════════════════════╗{Style.RESET_ALL}")
        print(f"{Fore.WHITE}║       📋 NOTIFICATION MENU            ║{Style.RESET_ALL}")
        print(f"{Fore.WHITE}╚═══════════════════════════════════════╝{Style.RESET_ALL}")
        print(f"{Fore.WHITE}1. ➕ Add Notification{Style.RESET_ALL}")
        print(f"{Fore.WHITE}2. 📋 View Notifications{Style.RESET_ALL}")
        print(f"{Fore.WHITE}3. 📤 Send Due Notifications Now{Style.RESET_ALL}")
        print(f"{Fore.WHITE}4. ✏️  Edit Notification{Style.RESET_ALL}")
        print(f"{Fore.WHITE}5. 🗑️  Delete Notification{Style.RESET_ALL}")
        print(f"{Fore.WHITE}6. 📬 Notification Services{Style.RESET_ALL}")
        print(f"{Fore.WHITE}0. 🚪 Exit{Style.RESET_ALL}")
        print(f"{Fore.WHITE}{'─'*43}{Style.RESET_ALL}")
        
        choice = input(f"{Fore.YELLOW}Choose an option: {Style.RESET_ALL}").strip()
        
        if choice == "1":
            add_notification()
        elif choice == "2":
            view_notifications()
        elif choice == "3":
            send_notifications()
        elif choice == "4":
            edit_notification()
        elif choice == "5":
            delete_notification()
        elif choice == "6":
            notification_services_menu()
        elif choice == "0":
            print(f"\n{Fore.GREEN}👋 Goodbye! Background scheduler will stop.{Style.RESET_ALL}")
            break
        else:
            print(f"{Fore.RED}❌ Invalid choice. Please try again.{Style.RESET_ALL}")

if __name__ == "__main__":
    main()