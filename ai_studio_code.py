import logging
import requests
import json
import time
import sqlite3
import re
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
)

# --- Configuration ---
# Get your bot token from @BotFather on Telegram
TELEGRAM_BOT_TOKEN = "8212174034:AAE4kQkplcNB93XGxmarxbHiErv4m9IjpmY" 
# Pollinations AI API URL (using qwen-coder model)
POLLINATIONS_API_BASE_URL = "https://text.pollinations.ai/"

# Admin User IDs - REPLACE WITH YOUR TELEGRAM USER ID(s)
ADMIN_USER_IDS = [6403299013] # Example: [123456789, 987654321]

# Conversation History Settings
MAX_HISTORY_MESSAGES = 30 # Number of previous messages (pairs of user-bot) to remember
# Rate Limiting Settings
MESSAGE_COOLDOWN_SECONDS = 1 # Minimum time between messages from the same user

# --- Bot Identity ---
BOT_IDENTITY = {
    'name': 'KCL AI°',
    'developer': '@Riajul6164',
    'purpose': 'Helping Users with Information to Prevent Scams on Telegram, And for Fuck*** Scammers.',
    'capabilities': [
        'Answers any Type of Legal / Illegal Secure Questions',
        'Find Information from Underground Source', # Note: Actual capability depends on underlying AI model
        'Counting and Solving Complex Mathematical Problem in Easy Way',
        'Provide Answers in Formatted Way Like ChatGPT'
    ],
    'credit': 'CEO: KCL✓Raad'
}

# --- Database Setup ---
DB_NAME = 'kcl_ai_bot.db'

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            last_active DATETIME,
            is_admin BOOLEAN DEFAULT FALSE
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            role TEXT, -- 'user' or 'bot'
            message TEXT,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    ''')
    conn.commit()
    conn.close()

def save_user(user_data, is_admin=False):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''
        INSERT OR REPLACE INTO users (user_id, username, first_name, last_name, last_active, is_admin)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (
        user_data.id,
        user_data.username,
        user_data.first_name,
        user_data.last_name,
        datetime.now().isoformat(),
        is_admin
    ))
    conn.commit()
    conn.close()

def get_user(user_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    user = c.fetchone()
    conn.close()
    return user

def get_all_users():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('SELECT * FROM users')
    users = c.fetchall()
    conn.close()
    return users

def save_chat_message(user_id, role, message):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''
        INSERT INTO chat_history (user_id, role, message)
        VALUES (?, ?, ?)
    ''', (user_id, role, message))
    conn.commit()
    conn.close()

def get_user_chat_history(user_id, limit=None):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    query = 'SELECT role, message FROM chat_history WHERE user_id = ? ORDER BY timestamp ASC'
    if limit:
        query += f' LIMIT {limit}'
    c.execute(query, (user_id,))
    history = c.fetchall()
    conn.close()
    return history

def get_last_message_time(user_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('SELECT timestamp FROM chat_history WHERE user_id = ? ORDER BY timestamp DESC LIMIT 1', (user_id,))
    result = c.fetchone()
    conn.close()
    if result:
        return datetime.fromisoformat(result[0])
    return None

# --- Basic In-Memory Knowledge Base (for RAG demonstration) ---
# In a real-world scenario, this would be a vector database with embeddings.
KNOWLEDGE_BASE_DATA = [
    "Scam Prevention Tip: Always verify the identity of someone asking for money online, even if they claim to be a friend or family member. Call them directly.",
    "Scam Prevention Tip: Be wary of unsolicited messages promising large sums of money, job offers, or lottery wins. These are often phishing attempts.",
    "Scam Prevention Tip: Never share your OTP (One-Time Password), bank account details, or credit card information with anyone, especially over unofficial channels.",
    "Scam Prevention Tip: Use strong, unique passwords for all your online accounts and enable two-factor authentication (2FA) wherever possible.",
    "Scam Prevention Tip: Research companies or individuals before making any investments or purchases, especially if the offer seems too good to be true.",
    "Scam Prevention Tip: Government agencies will never ask for personal information or payment via unofficial emails or text messages.",
    "KCL AI is developed by @Riajul6164.",
    "The CEO of KCL {BD} • is KCL✓Raad.",
    "KCL AI aims to fight against scammers on Telegram and help users with security information."
]

def search_knowledge_base(query):
    """
    A very basic keyword-based search for demonstration.
    For production, integrate a proper RAG system (embeddings + vector DB).
    """
    relevant_chunks = []
    query_lower = query.lower()
    for chunk in KNOWLEDGE_BASE_DATA:
        if any(keyword in chunk.lower() for keyword in query_lower.split()):
            relevant_chunks.append(chunk)
    return relevant_chunks[:3] # Return top 3 most relevant (by this simple metric)


# --- Logging ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Global Data ---
# Stores the last message time for rate limiting per user
last_message_time = {}

# --- Helper Functions ---
def is_admin(user_id):
    return user_id in ADMIN_USER_IDS

def format_bot_identity():
    capabilities_str = "\n".join([f"- {cap}" for cap in BOT_IDENTITY['capabilities']])
    return (
        f"You are {BOT_IDENTITY['name']}, a helpful AI assistant.\n"
        f"Your developer is {BOT_IDENTITY['developer']}.\n"
        f"Your core purpose is: {BOT_IDENTITY['purpose']}\n"
        f"Your capabilities include:\n{capabilities_str}\n"
        f"Credit goes to {BOT_IDENTITY['credit']}.\n"
        "You should always answer in English and in a formatted way like ChatGPT."
    )

async def send_typing_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')


# --- Telegram Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    save_user(user, is_admin(user.id)) # Save/update user info
    
    reply_markup = ReplyKeyboardMarkup([
        [KeyboardButton("Ask KCL AI")],
        [KeyboardButton("About KCL AI"), KeyboardButton("Scam Prevention Tips")]
    ], resize_keyboard=True)

    await update.message.reply_html(
        f"Hello {user.mention_html()}! I'm {BOT_IDENTITY['name']}. "
        f"I'm here to help you with information to prevent scams on Telegram.\n\n"
        f"Type /help to see what I can do.",
        reply_markup=reply_markup
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    help_text = (
        f"I am {BOT_IDENTITY['name']}.\n"
        f"My purpose is: {BOT_IDENTITY['purpose']}\n"
        f"My capabilities are:\n"
    )
    for cap in BOT_IDENTITY['capabilities']:
        help_text += f"• {cap}\n"
    help_text += "\n"
    help_text += "You can ask me anything, or use these commands:\n"
    help_text += "/start - Start the bot and see main options\n"
    help_text += "/help - Show this help message\n"
    help_text += "/about - Learn more about me\n"

    if is_admin(user_id):
        help_text += "\n--- Admin Commands ---\n"
        help_text += "/admin - Access the admin panel\n"
        help_text += "/users - View all users\n"
        help_text += "/viewuser <user_id> - View specific user profile\n"
        help_text += "/viewchats <user_id> - View specific user's chat history\n"
    
    await update.message.reply_text(help_text)

async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    about_text = format_bot_identity()
    await update.message.reply_text(about_text)

async def scam_tips_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tips = [chunk for chunk in KNOWLEDGE_BASE_DATA if "Scam Prevention Tip" in chunk]
    if tips:
        message = "Here are some scam prevention tips:\n\n" + "\n\n".join(tips)
    else:
        message = "No scam prevention tips found in the knowledge base."
    await update.message.reply_text(message)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_id = user.id
    user_message = update.message.text

    save_user(user, is_admin(user_id)) # Ensure user is updated in DB

    # Handle direct button presses for known commands
    if user_message.lower() == "about kcl ai":
        await about_command(update, context)
        return
    elif user_message.lower() == "scam prevention tips":
        await scam_tips_command(update, context)
        return
    elif user_message.lower() == "ask kcl ai":
        await update.message.reply_text("What would you like to ask me?")
        return

    # --- Rate Limiting Check ---
    now = datetime.now()
    if user_id in last_message_time and (now - last_message_time[user_id]).total_seconds() < MESSAGE_COOLDOWN_SECONDS:
        await update.message.reply_text("Please wait a moment before sending another message.")
        return
    last_message_time[user_id] = now

    await send_typing_action(update, context)

    # --- Retrieve relevant info from Knowledge Base (RAG-like) ---
    # In a real RAG system, this would involve embeddings and a vector DB.
    # For now, a simple keyword search.
    relevant_kb_info = search_knowledge_base(user_message)
    kb_context = ""
    if relevant_kb_info:
        kb_context = "Here is some relevant background information that might help you answer the user's question:\n" + "\n".join(relevant_kb_info) + "\n\n"

    # --- Prepare Chat History for Context ---
    # Fetch last N messages from DB for context
    chat_history_db = get_user_chat_history(user_id, limit=MAX_HISTORY_MESSAGES * 2) # Get up to N pairs
    
    conversation_context = []
    for role, msg in chat_history_db:
        if role == 'user':
            conversation_context.append(f"User: {msg}")
        elif role == 'bot':
            conversation_context.append(f"KCL AI: {msg}")
    
    history_str = "\n".join(conversation_context)
    if history_str:
        history_str = "\n\nPrevious conversation history:\n" + history_str + "\n"

    # --- Construct Full Prompt for Pollinations AI ---
    system_prompt = format_bot_identity()

    full_prompt = (
        f"{system_prompt}\n\n"
        f"{kb_context}" # Inject KB context if available
        f"{history_str}" # Inject conversation history
        f"User: {user_message}\n"
        f"KCL AI:" # Encourage AI to respond as KCL AI
    )

    logger.info(f"Generated prompt for user {user_id}: {full_prompt}")

    try:
        # Encode prompt for URL
        encoded_query = requests.utils.quote(full_prompt)
        api_url = f"{POLLINATIONS_API_BASE_URL}{encoded_query}?model=qwen-coder"
        
        response = requests.get(api_url, timeout=30) # Add timeout for API call
        response.raise_for_status() # Raise an exception for HTTP errors
        
        ai_response_text = response.text.strip()
        
        if ai_response_text:
            # Clean up potential prefix/suffix from AI (e.g., if it repeats "KCL AI:")
            ai_response_text = re.sub(r'^(KCL AI:\s*)+', '', ai_response_text, flags=re.IGNORECASE).strip()
            
            await update.message.reply_text(ai_response_text, parse_mode='Markdown') # Use Markdown for formatting
            
            # Save user message and bot response to DB
            save_chat_message(user_id, 'user', user_message)
            save_chat_message(user_id, 'bot', ai_response_text)
        else:
            await update.message.reply_text("I'm sorry, I couldn't generate a response for that. Please try again.")

    except requests.exceptions.RequestException as e:
        logger.error(f"Error calling Pollinations AI API for user {user_id}: {e}")
        await update.message.reply_text("I'm having trouble connecting to my AI brain. Please try again in a moment.")
    except Exception as e:
        logger.error(f"An unexpected error occurred for user {user_id}: {e}")
        await update.message.reply_text("An unexpected error occurred. Please try again later.")

# --- Admin Panel Handlers ---

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("You are not authorized to use admin commands.")
        return

    keyboard = [
        [InlineKeyboardButton("View All Users", callback_data='admin_view_users')],
        [InlineKeyboardButton("View User by ID", callback_data='admin_prompt_user_id')],
        [InlineKeyboardButton("Back to Main Menu", callback_data='admin_main_menu')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Welcome to the Admin Panel!", reply_markup=reply_markup)

async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer() # Acknowledge the callback query

    if not is_admin(user_id):
        await query.edit_message_text("You are not authorized to use admin commands.")
        return

    data = query.data

    if data == 'admin_view_users':
        await view_all_users_admin(query, context)
    elif data == 'admin_prompt_user_id':
        await context.bot.send_message(user_id, "Please send me the User ID you want to view (e.g., 123456789):")
        context.user_data['awaiting_user_id_for_admin_view'] = True
    elif data.startswith('admin_view_profile_'):
        target_user_id = int(data.split('_')[3])
        await view_user_profile_admin(query, context, target_user_id)
    elif data.startswith('admin_view_chats_'):
        target_user_id = int(data.split('_')[3])
        await view_user_chats_admin(query, context, target_user_id)
    elif data == 'admin_main_menu':
        await admin_command(query, context) # Re-show admin panel


async def view_all_users_admin(query: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    users = get_all_users()
    if not users:
        await query.edit_message_text("No users registered yet.")
        return

    message_parts = ["*Registered Users:*\n"]
    keyboard_buttons = []

    for user_data in users:
        u_id, u_username, u_first_name, u_last_name, u_last_active, u_is_admin = user_data
        
        display_name = u_first_name or "N/A"
        if u_last_name:
            display_name += f" {u_last_name}"
        if u_username:
            display_name += f" (@{u_username})"
        
        message_parts.append(
            f"ID: `{u_id}`\n"
            f"Name: {display_name}\n"
            f"Admin: {'Yes' if u_is_admin else 'No'}\n"
            f"Last Active: {u_last_active}\n"
            f"---------------------\n"
        )
        keyboard_buttons.append([
            InlineKeyboardButton(f"Profile: {display_name}", callback_data=f'admin_view_profile_{u_id}'),
            InlineKeyboardButton(f"Chats: {display_name}", callback_data=f'admin_view_chats_{u_id}')
        ])
    
    keyboard_buttons.append([InlineKeyboardButton("Back to Admin Panel", callback_data='admin_main_menu')])
    reply_markup = InlineKeyboardMarkup(keyboard_buttons)

    # Telegram messages have a length limit. Split if necessary.
    full_message = "".join(message_parts)
    if len(full_message) > 4096:
        # Simple splitting: take the first part, then send the rest
        await query.edit_message_text(full_message[:4000] + "...", parse_mode='Markdown')
        await context.bot.send_message(query.from_user.id, "..." + full_message[4000:], parse_mode='Markdown', reply_markup=reply_markup)
    else:
        await query.edit_message_text(full_message, parse_mode='Markdown', reply_markup=reply_markup)


async def admin_process_user_id_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not is_admin(user_id) or not context.user_data.get('awaiting_user_id_for_admin_view'):
        return

    try:
        target_user_id = int(update.message.text.strip())
        del context.user_data['awaiting_user_id_for_admin_view']
        
        user_data = get_user(target_user_id)
        if not user_data:
            await update.message.reply_text(f"No user found with ID: `{target_user_id}`.", parse_mode='Markdown')
            return

        u_id, u_username, u_first_name, u_last_name, u_last_active, u_is_admin = user_data
        
        profile_text = (
            f"*User Profile (ID: `{u_id}`)*\n"
            f"First Name: {u_first_name or 'N/A'}\n"
            f"Last Name: {u_last_name or 'N/A'}\n"
            f"Username: @{u_username or 'N/A'}\n"
            f"Admin Status: {'Yes' if u_is_admin else 'No'}\n"
            f"Last Active: {u_last_active}\n"
        )
        
        keyboard = [
            [InlineKeyboardButton(f"View Chats of {u_first_name}", callback_data=f'admin_view_chats_{u_id}')],
            [InlineKeyboardButton("Back to Admin Panel", callback_data='admin_main_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(profile_text, parse_mode='Markdown', reply_markup=reply_markup)

    except ValueError:
        await update.message.reply_text("Invalid User ID. Please enter a valid number.")
    except Exception as e:
        logger.error(f"Error processing user ID for admin view: {e}")
        await update.message.reply_text("An error occurred while fetching user data.")


async def view_user_profile_admin(query: Update, context: ContextTypes.DEFAULT_TYPE, target_user_id: int) -> None:
    user_data = get_user(target_user_id)
    if not user_data:
        await query.edit_message_text(f"No user found with ID: `{target_user_id}`.", parse_mode='Markdown')
        return

    u_id, u_username, u_first_name, u_last_name, u_last_active, u_is_admin = user_data
    
    profile_text = (
        f"*User Profile (ID: `{u_id}`)*\n"
        f"First Name: {u_first_name or 'N/A'}\n"
        f"Last Name: {u_last_name or 'N/A'}\n"
        f"Username: @{u_username or 'N/A'}\n"
        f"Admin Status: {'Yes' if u_is_admin else 'No'}\n"
        f"Last Active: {u_last_active}\n"
    )
    
    keyboard = [
        [InlineKeyboardButton(f"View Chats of {u_first_name}", callback_data=f'admin_view_chats_{u_id}')],
        [InlineKeyboardButton("Back to Admin Panel", callback_data='admin_main_menu')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(profile_text, parse_mode='Markdown', reply_markup=reply_markup)


async def view_user_chats_admin(query: Update, context: ContextTypes.DEFAULT_TYPE, target_user_id: int) -> None:
    chat_history = get_user_chat_history(target_user_id)
    user_data = get_user(target_user_id)
    user_name = user_data[2] if user_data else f"User {target_user_id}"

    if not chat_history:
        await query.edit_message_text(f"No chat history found for {user_name} (ID: `{target_user_id}`).", parse_mode='Markdown')
        return

    message_parts = [f"*Chat History for {user_name} (ID: `{target_user_id}`):*\n\n"]
    
    for role, message in chat_history:
        if role == 'user':
            message_parts.append(f"*User:* {message}\n")
        elif role == 'bot':
            message_parts.append(f"*KCL AI:* {message}\n")
        message_parts.append("---\n") # Separator for readability

    keyboard = [
        [InlineKeyboardButton(f"View Profile of {user_name}", callback_data=f'admin_view_profile_{target_user_id}')],
        [InlineKeyboardButton("Back to Admin Panel", callback_data='admin_main_menu')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Split message if too long for Telegram's 4096 character limit
    full_message = "".join(message_parts)
    if len(full_message) > 4096:
        # Send first part with buttons, then subsequent parts without
        await query.edit_message_text(full_message[:4000] + "...", parse_mode='Markdown')
        remaining_message = full_message[4000:]
        while len(remaining_message) > 0:
            part = remaining_message[:4000]
            await context.bot.send_message(query.from_user.id, "..." + part, parse_mode='Markdown')
            remaining_message = remaining_message[4000:]
        await context.bot.send_message(query.from_user.id, "End of chat history.", reply_markup=reply_markup)
    else:
        await query.edit_message_text(full_message, parse_mode='Markdown', reply_markup=reply_markup)


def main() -> None:
    # Initialize the database
    init_db()

    # Build the Application and register handlers
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # User Commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("about", about_command))
    application.add_handler(CommandHandler("scam_tips", scam_tips_command)) # Direct command for tips

    # Admin Commands
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("users", view_all_users_admin)) # Direct command for listing users
    application.add_handler(CommandHandler("viewuser", lambda u, c: c.user_data.update({'awaiting_user_id_for_admin_view': True}) or u.message.reply_text("Please send me the User ID you want to view:") if is_admin(u.effective_user.id) else u.message.reply_text("You are not authorized to use admin commands.")))
    application.add_handler(CommandHandler("viewchats", lambda u, c: c.user_data.update({'awaiting_user_id_for_admin_view': True, 'action': 'view_chats'}) or u.message.reply_text("Please send me the User ID whose chats you want to view:") if is_admin(u.effective_user.id) else u.message.reply_text("You are not authorized to use admin commands.")))


    # Message Handler for general text and admin user ID input
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.User(lambda user: not user.id in ADMIN_USER_IDS or not context.user_data.get('awaiting_user_id_for_admin_view')), 
        handle_message
    ))
    # Specific handler for admin's user ID input for viewuser/viewchats
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.User(ADMIN_USER_IDS) & filters.Chat(lambda chat: chat.id in ADMIN_USER_IDS if 'awaiting_user_id_for_admin_view' in context.user_data else False),
        admin_process_user_id_input
    ))


    # Callback Query Handler for inline buttons (Admin Panel)
    application.add_handler(CallbackQueryHandler(admin_callback_handler, pattern=r'^admin_'))

    # Start the bot
    logger.info("KCL AI Bot started polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    # Initialize `context.user_data` if it's not automatically managed.
    # For `run_polling`, `ContextTypes.DEFAULT_TYPE` handles this.
    main()