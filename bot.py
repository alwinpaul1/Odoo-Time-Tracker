import os
import logging
import datetime
import calendar
import subprocess
import tempfile
import signal
import threading
import matplotlib
import json
import re
from collections import namedtuple
from typing import Dict, List, Optional, Tuple, Union, Any
# Set non-interactive backend for matplotlib
matplotlib.use('Agg')
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, ConversationHandler, MessageHandler, filters
from dotenv import load_dotenv
import plot_times
import requests
from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
import time

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Define weekday keys
WEEKDAY_KEYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# States for conversation handler
CHOOSING_ACTION = 0
WAITING_FOR_CUSTOM_MONTH = 1
WAITING_FOR_SESSION_ID = 2
WAITING_FOR_CSRF_TOKEN = 3
WAITING_FOR_ODOO_UID = 4
CHOOSING_WORK_SCHEDULE = 5
SETTING_WORK_DAYS = 6
SETTING_WORK_HOURS = 7
WAITING_FOR_HOURS_INPUT = 8
WAITING_FOR_ALL_HOURS_INPUT = 9
WAITING_FOR_EMAIL = 10
WAITING_FOR_PASSWORD = 11

# Default work schedule settings
DEFAULT_WORK_DAYS = {
    "Mon": True,
    "Tue": True,
    "Wed": True,
    "Thu": True,
    "Fri": True,
    "Sat": False,
    "Sun": False
}

DEFAULT_WORK_HOURS = {
    "Mon": 8.0,
    "Tue": 8.0,
    "Wed": 8.0,
    "Thu": 8.0,
    "Fri": 8.0,
    "Sat": 0.0,
    "Sun": 0.0
}

# Work schedule types
FULL_TIME = "full_time"  # 40 hours per week
PART_TIME = "part_time"  # 20 hours per week
CUSTOM = "custom"        # Custom hours per day

# Get the bot token from environment variable
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

# Environment variable for storing credentials
CREDENTIALS_ENV_VAR = 'USER_CREDENTIALS'

# Load existing credentials or create empty dict
def load_credentials():
    try:
        # Try to get credentials from Heroku config vars first
        if os.environ.get('DYNO'):  # Check if running on Heroku
            credentials_json = os.getenv(CREDENTIALS_ENV_VAR)
            if not credentials_json:
                # If not in env var, try to load from local file as fallback
                if os.path.exists('credentials.json'):
                    with open('credentials.json', 'r') as f:
                        credentials = json.load(f)
                        logger.info(f"Loaded credentials for {len(credentials)} users from local file")
                        return credentials
            else:
                credentials = json.loads(credentials_json)
                logger.info(f"Loaded credentials for {len(credentials)} users from Heroku config var")
                return credentials
        else:
            # Local development - use local file
            if os.path.exists('credentials.json'):
                with open('credentials.json', 'r') as f:
                    credentials = json.load(f)
                    logger.info(f"Loaded credentials for {len(credentials)} users from local file")
                    return credentials
        
        logger.warning(f"No credentials found, creating new empty dictionary")
        return {}
    except Exception as e:
        logger.error(f"Error loading credentials: {e}")
        return {}

# Save credentials to environment variable
def save_credentials(credentials):
    try:
        credentials_json = json.dumps(credentials)
        if os.environ.get('DYNO'):  # Check if running on Heroku
            # Save to both env var and file for redundancy
            os.environ[CREDENTIALS_ENV_VAR] = credentials_json
            with open('credentials.json', 'w') as f:
                json.dump(credentials, f)
            logger.info("Saved credentials to Heroku env var and local file")
            
            # Update Heroku config var using API
            try:
                heroku_api_key = os.getenv('HEROKU_API_KEY')
                if heroku_api_key:
                    # Get app name from environment or use default
                    app_name = os.getenv('HEROKU_APP_NAME', 'odoo-time-tracking-tool')
                    logger.info(f"Using Heroku app name: {app_name}")
                    
                    headers = {
                        'Accept': 'application/vnd.heroku+json; version=3',
                        'Authorization': f'Bearer {heroku_api_key}',
                        'Content-Type': 'application/json'
                    }
                    url = f'https://api.heroku.com/apps/{app_name}/config-vars'
                    data = {CREDENTIALS_ENV_VAR: credentials_json}
                    
                    logger.info(f"Updating Heroku config var with API: {url}")
                    response = requests.patch(url, headers=headers, json=data)
                    
                    if response.status_code == 200:
                        logger.info("Successfully updated Heroku config var")
                    else:
                        logger.error(f"Failed to update Heroku config var: {response.status_code}, {response.text}")
            except Exception as e:
                logger.error(f"Error updating Heroku config var: {e}")
        else:
            # Local development - save to file
            with open('credentials.json', 'w') as f:
                json.dump(credentials, f)
            logger.info("Saved credentials to local file")
    except Exception as e:
        logger.error(f"Error saving credentials: {e}")

# Global variables
user_credentials = load_credentials()
logger.info(f"Loaded credentials for users: {list(user_credentials.keys())}")

class TimeoutException(Exception):
    """Exception raised when a function call times out."""
    pass

def timeout_handler(signum, frame):
    """Handler for timeout signal."""
    raise TimeoutException("Function call timed out")

def run_with_timeout(func, timeout=60, *args, **kwargs):
    """Run a function with a timeout."""
    # Set the timeout handler
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(timeout)
    
    try:
        result = func(*args, **kwargs)
        # Cancel the alarm if the function returns before timeout
        signal.alarm(0)
        return result
    except TimeoutException:
        # Handle the timeout
        raise
    finally:
        # Reset the alarm
        signal.alarm(0)

def get_formatted_work_schedule(user_id):
    """Get a formatted string with work schedule details for the given user."""
    if str(user_id) not in user_credentials or 'work_schedule' not in user_credentials[str(user_id)]:
        return "❌ No work schedule set"
    
    work_schedule = user_credentials[str(user_id)]['work_schedule']
    schedule_type = work_schedule.get('type', 'custom')
    
    if schedule_type == FULL_TIME:
        return "✅ Work schedule: Full Time (40h/week)"
    elif schedule_type == PART_TIME:
        return "✅ Work schedule: Part Time (20h/week)"
    else:
        # Custom schedule - show days and hours
        work_days = work_schedule.get('days', {})
        work_hours = work_schedule.get('hours', {})
        
        # Get day full names for display
        day_full_names = {
            "Mon": "Monday",
            "Tue": "Tuesday",
            "Wed": "Wednesday",
            "Thu": "Thursday",
            "Fri": "Friday",
            "Sat": "Saturday",
            "Sun": "Sunday"
        }
        
        # Get enabled days in correct order
        day_order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        enabled_days = sorted(
            [day for day, enabled in work_days.items() if enabled],
            key=lambda x: day_order.index(x)
        )
        
        # Format days with hours
        days_with_hours = []
        for day in enabled_days:
            hours = work_hours.get(day, 0.0)
            if hours > 0:
                days_with_hours.append(f"{day}: {hours}h")
        
        days_text = ", ".join(days_with_hours)
        total_hours = sum([hours for day, hours in work_hours.items() if work_days.get(day, False)])
        
        return f"✅ Work schedule: {total_hours:.1f}h/week ({days_text})"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Send a welcome message when the command /start is issued."""
    user_id = update.effective_user.id
    logger.info(f"User {user_id} started the bot")
    
    keyboard = [
        [
            InlineKeyboardButton("📅 Current Month", callback_data="month"),
            InlineKeyboardButton("📊 Current Week", callback_data="week"),
        ],
        [
            InlineKeyboardButton("🗓️ Custom Month", callback_data="custom_month"),
            InlineKeyboardButton("📈 Status", callback_data="status"),
        ],
        [
            InlineKeyboardButton("🔑 Set Credentials", callback_data="set_credentials"),
            InlineKeyboardButton("🔄 Auto Fetch Tokens", callback_data="auto_fetch_tokens"),
        ],
        [
            InlineKeyboardButton("⏰ Work Schedule", callback_data="work_schedule"),
            InlineKeyboardButton("❓ Help", callback_data="help"),
        ]
    ]
    
    # Check if user has credentials
    has_credentials = str(user_id) in user_credentials
    credential_status = "✅ Credentials set" if has_credentials else "❌ No credentials set"
    
    # Get formatted work schedule status
    work_schedule_status = get_formatted_work_schedule(user_id)
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"Welcome to the Odoo Time Tracking Bot!\n\n{credential_status}\n{work_schedule_status}\n\nWhat would you like to do?",
        reply_markup=reply_markup
    )
    
    return CHOOSING_ACTION

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle button presses."""
    query = update.callback_query
    await query.answer()
    
    logger.info(f"User {query.from_user.id} pressed button: {query.data}")
    
    if query.data == "month":
        await generate_report(update, context, plot_month=True)
        return ConversationHandler.END
    elif query.data == "week":
        await generate_report(update, context, plot_week=True)
        return ConversationHandler.END
    elif query.data == "custom_month":
        logger.info(f"User {query.from_user.id} selected custom month option")
        # Store the fact that we're waiting for custom month input
        context.user_data['awaiting_custom_month'] = True
        
        # Edit the message to prompt for custom month input
        await query.edit_message_text(
            "Please send the month in format YYYY-MM (e.g., 2024-12) or just the month number (1-12)"
        )
        # IMPORTANT: Return the state to transition to
        return WAITING_FOR_CUSTOM_MONTH
    elif query.data == "status":
        await generate_report(update, context, status_only=True)
        return ConversationHandler.END
    elif query.data == "set_credentials":
        await query.edit_message_text(
            "Please enter your Odoo Session ID"
        )
        return WAITING_FOR_SESSION_ID
    elif query.data == "auto_fetch_tokens":
        await query.edit_message_text(
            "Please enter your Odoo email address"
        )
        # Store the default URL in context
        context.user_data['odoo_url'] = "https://perinet.odoo.com/web"
        return WAITING_FOR_EMAIL
    elif query.data == "work_schedule":
        return await show_work_schedule_options(update, context)
    elif query.data == "help":
        help_text = (
            "🤖 *Odoo Time Tracking Bot* 🤖\n\n"
            "*📋 Available Commands:*\n"
            "• `/start` - Start the bot and show main menu\n"
            "• `/month` - Generate report for current month\n"
            "• `/week` - Generate report for current week\n"
            "• `/custom YYYY-MM` - Generate report for specific month\n"
            "• `/status` - Show current status without PDF\n"
            "• `/credentials` - Set your Odoo credentials\n"
            "• `/work_schedule` - Set your work schedule\n"
            "• `/debug` - Show your credential status\n"
            "• `/help` - Show this help message\n\n"
            "*📊 Report Types:*\n"
            "• *Monthly Report* - Complete analysis with PDF chart\n"
            "• *Weekly Report* - Current week's hours with chart\n"
            "• *Custom Month* - Specify any month (YYYY-MM)\n"
            "• *Status* - Quick text-only summary\n\n"
            "*⚙️ Getting Started:*\n"
            "1. Set your credentials with `/credentials`\n"
            "2. Set your work schedule with `/work_schedule`\n"
            "3. Generate your first report with `/month`\n"
            "4. Check your status anytime with `/status`\n\n"
            "This bot helps you track your work hours from Odoo and visualize them."
        )
        await query.edit_message_text(help_text, parse_mode="Markdown")
        
        # Show menu buttons after help
        await show_menu_buttons(update, context)
        
        return ConversationHandler.END
    # Handle work schedule options
    elif query.data == "full_time":
        return await set_work_schedule(update, context, FULL_TIME)
    elif query.data == "part_time_custom":
        return await start_part_time_custom(update, context)
    elif query.data == "custom_schedule":
        return await start_custom_schedule(update, context)
    elif query.data.startswith("toggle_day_"):
        return await toggle_work_day(update, context, query.data.replace("toggle_day_", ""))
    elif query.data == "save_work_days":
        return await save_work_days(update, context)
    elif query.data == "save_part_time_days":
        return await save_part_time_days(update, context)
    elif query.data == "cancel_work_schedule":
        await query.edit_message_text("Work schedule setup cancelled.")
        await show_menu_buttons(update, context)
        return ConversationHandler.END
    # Handle work hours options
    elif query.data == "hours_full_time" or query.data == "hours_part_time":
        return await set_hours_distribution(update, context, query.data)
    elif query.data == "hours_standard":
        return await set_hours_distribution(update, context, "hours_part_time")
    elif query.data == "set_specific_hours":
        return await show_specific_hours_setup(update, context)
    elif query.data == "set_all_hours":
        return await set_all_hours_at_once(update, context)
    elif query.data.startswith("edit_hours_"):
        return await edit_day_hours(update, context, query.data.replace("edit_hours_", ""))
    elif query.data == "save_specific_hours":
        return await save_specific_hours(update, context)
    elif query.data == "back_to_days":
        return await show_work_days_selection(update, context)
    elif query.data == "back_to_hours_selection":
        return await save_work_days(update, context)
    
    # Default fallback
    return ConversationHandler.END

async def session_id_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle session ID input."""
    user_id = update.effective_user.id
    session_id = update.message.text.strip()
    
    # Validate session ID format - typically alphanumeric without special characters
    # Session IDs are usually long alphanumeric strings
    if not session_id or len(session_id) < 20 or not session_id.isalnum():
        await update.message.reply_text(
            "Invalid session ID format. Session IDs are typically long alphanumeric strings.\n\n"
            "Please enter a valid session ID. You can find this in your browser cookies after logging into Odoo."
        )
        return WAITING_FOR_SESSION_ID
    
    # Store in context for later
    context.user_data['session_id'] = session_id
    
    await update.message.reply_text("Great! Now please enter your CSRF Token")
    return WAITING_FOR_CSRF_TOKEN

async def csrf_token_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle CSRF token input."""
    user_id = update.effective_user.id
    csrf_token = update.message.text.strip()
    
    # Validate CSRF token format - typically a long alphanumeric string
    if not csrf_token or len(csrf_token) < 20:
        await update.message.reply_text(
            "Invalid CSRF token format. CSRF tokens are typically long strings.\n\n"
            "Please enter a valid CSRF token. You can find this in your browser cookies or page source after logging into Odoo."
        )
        return WAITING_FOR_CSRF_TOKEN
    
    # Store in context for later
    context.user_data['csrf_token'] = csrf_token
    
    await update.message.reply_text("Almost done! Now please enter your Odoo User ID (UID)")
    return WAITING_FOR_ODOO_UID

async def odoo_uid_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle Odoo UID input."""
    user_id = update.effective_user.id
    odoo_uid = update.message.text.strip()
    
    # Validate UID is a number
    try:
        odoo_uid = int(odoo_uid)
    except ValueError:
        await update.message.reply_text("User ID must be a number. Please try again.")
        return WAITING_FOR_ODOO_UID
    
    # Save all credentials
    global user_credentials
    user_credentials[str(user_id)] = {
        'session_id': context.user_data.get('session_id'),
        'csrf_token': context.user_data.get('csrf_token'),
        'odoo_uid': odoo_uid
    }
    
    # Save to environment variable
    save_credentials(user_credentials)
    logger.info(f"Saved credentials for user {user_id}")
    
    # Log the current state of credentials
    logger.info(f"Current credentials: {list(user_credentials.keys())}")
    
    await update.message.reply_text("Your credentials have been saved successfully! You can now generate reports.")
    
    # Show menu buttons after saving credentials
    await show_menu_buttons(update, context)
    
    return ConversationHandler.END

async def credentials_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Command to set credentials."""
    user_id = update.effective_user.id
    logger.info(f"User {user_id} is setting credentials")
    
    await update.message.reply_text("Please enter your Odoo Session ID")
    return WAITING_FOR_SESSION_ID

async def custom_month_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle text input for custom month."""
    user_id = update.effective_user.id
    custom_month = update.message.text.strip()
    
    logger.info(f"User {user_id} entered custom month: {custom_month}")
    
    # Check if we're actually waiting for custom month input
    if not context.user_data.get('awaiting_custom_month', False):
        logger.warning(f"User {user_id} sent text but we're not waiting for custom month input")
        # Just show the menu again
        await update.message.reply_text("Please use the menu buttons to select an option.")
        await show_menu_buttons(update, context)
        return ConversationHandler.END
    
    # Reset the flag
    context.user_data['awaiting_custom_month'] = False
    
    # Check if input is just a month number (1-12)
    if custom_month.isdigit() and 1 <= int(custom_month) <= 12:
        # Convert to YYYY-MM format using current year
        current_year = datetime.datetime.now().year
        custom_month = f"{current_year}-{int(custom_month):02d}"
        logger.info(f"Converted month input to: {custom_month}")
    
    try:
        year, month = map(int, custom_month.split('-'))
        if not (1 <= month <= 12):
            raise ValueError("Month must be between 1 and 12")
    except ValueError as e:
        logger.warning(f"User {user_id} provided invalid month format: {custom_month}, error: {str(e)}")
        await update.message.reply_text("Invalid format. Please use YYYY-MM (e.g., 2024-12) or just the month number (1-12)")
        # Set the flag again since we're still waiting for input
        context.user_data['awaiting_custom_month'] = True
        return WAITING_FOR_CUSTOM_MONTH
    
    try:
        logger.info(f"Generating report for custom month: {custom_month}")
        # Store the custom month in context to ensure it's available
        context.user_data['custom_month'] = custom_month
        
        # Send a message to indicate we're processing
        processing_message = await update.message.reply_text(f"Generating report for {custom_month}... Please wait.")
        
        # Generate the report with the custom month
        await generate_report(update, context, custom_month=custom_month)
        
        # End the conversation
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Error generating custom month report: {e}", exc_info=True)
        await update.message.reply_text(f"Error generating report: {str(e)}")
        
        # Show menu buttons even after error
        await show_menu_buttons(update, context)
        
        return ConversationHandler.END

async def month_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate report for the current month."""
    await generate_report(update, context, plot_month=True)

async def week_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate report for the current week."""
    await generate_report(update, context, plot_week=True)

async def custom_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate report for a custom month."""
    if not context.args:
        await update.message.reply_text("Please provide a month in format YYYY-MM (e.g., /custom 2024-12)")
        return
    
    custom_month = context.args[0]
    try:
        year, month = map(int, custom_month.split('-'))
        if not (1 <= month <= 12):
            raise ValueError
    except ValueError:
        await update.message.reply_text("Invalid format. Please use YYYY-MM (e.g., 2024-12)")
        return
    
    await generate_report(update, context, custom_month=custom_month)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show current status without generating PDF."""
    await generate_report(update, context, status_only=True)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a help message."""
    help_text = (
        "🤖 *Odoo Time Tracking Bot* 🤖\n\n"
        "*📋 Available Commands:*\n"
        "• `/start` - Start the bot and show main menu\n"
        "• `/month` - Generate report for current month\n"
        "• `/week` - Generate report for current week\n"
        "• `/custom YYYY-MM` - Generate report for specific month\n"
        "• `/status` - Show current status without PDF\n"
        "• `/credentials` - Set your Odoo credentials\n"
        "• `/work_schedule` - Set your work schedule\n"
        "• `/debug` - Show your credential status\n"
        "• `/help` - Show this help message\n\n"
        "*📊 Report Types:*\n"
        "• *Monthly Report* - Complete analysis with PDF chart\n"
        "• *Weekly Report* - Current week's hours with chart\n"
        "• *Custom Month* - Specify any month (YYYY-MM)\n"
        "• *Status* - Quick text-only summary\n\n"
        "*⚙️ Getting Started:*\n"
        "1. Set your credentials with `/credentials`\n"
        "2. Set your work schedule with `/work_schedule`\n"
        "3. Generate your first report with `/month`\n"
        "4. Check your status anytime with `/status`\n\n"
        "This bot helps you track your work hours from Odoo and visualize them."
    )
    
    await update.message.reply_text(help_text, parse_mode="Markdown")
    
    # Show menu buttons after help
    await show_menu_buttons(update, context)

async def debug_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Debug command to show current credentials."""
    user_id = update.effective_user.id
    
    # Only show debug info for the requesting user
    has_credentials = str(user_id) in user_credentials
    
    debug_text = f"Debug Info:\n\n"
    debug_text += f"Your User ID: {user_id}\n"
    debug_text += f"Credentials set: {has_credentials}\n"
    debug_text += f"Total users with credentials: {len(user_credentials)}\n"
    debug_text += f"Users with credentials: {list(user_credentials.keys())}\n"
    
    if has_credentials:
        creds = user_credentials[str(user_id)]
        debug_text += f"\nYour credentials:\n"
        debug_text += f"Session ID: {creds['session_id'][:10]}...\n"
        debug_text += f"CSRF Token: {creds['csrf_token'][:10]}...\n"
        debug_text += f"UID: {creds['odoo_uid']}\n"
        
        # Add work schedule info if available
        if 'work_schedule' in creds:
            work_schedule = creds['work_schedule']
            schedule_type = work_schedule.get('type', 'custom')
            work_days = work_schedule.get('days', {})
            work_hours = work_schedule.get('hours', {})
            
            debug_text += f"\nYour work schedule:\n"
            debug_text += f"Type: {schedule_type}\n"
            
            # Show work days
            work_days_list = [day for day, enabled in work_days.items() if enabled]
            debug_text += f"Work days: {', '.join(work_days_list)}\n"
            
            # Show hours per day
            debug_text += "Hours per day:\n"
            for day in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]:
                if work_days.get(day, False):
                    debug_text += f"- {day}: {work_hours.get(day, 0.0)}h\n"
            
            # Calculate total hours
            total_hours = sum(hours for day, hours in work_hours.items() if work_days.get(day, False))
            debug_text += f"Total hours per week: {total_hours}h\n"
        else:
            debug_text += "\nNo work schedule set. Use /work_schedule to set it up.\n"
    
    await update.message.reply_text(debug_text)
    
    # Show menu buttons after debug
    await show_menu_buttons(update, context)

async def generate_report(update: Update, context: ContextTypes.DEFAULT_TYPE, 
                          plot_month=False, plot_week=False, custom_month=None, 
                          status_only=False) -> None:
    """Generate the time tracking report based on specified parameters."""
    # Determine if we're responding to a callback query or a direct command
    is_callback = update.callback_query is not None
    user_id = update.effective_user.id if not is_callback else update.callback_query.from_user.id
    
    logger.info(f"Generating report for user {user_id}")
    logger.info(f"Current credentials: {list(user_credentials.keys())}")
    
    # Check if user has credentials
    if str(user_id) not in user_credentials:
        message_text = "You haven't set your Odoo credentials yet. Please use /credentials to set them up."
        logger.warning(f"User {user_id} has no credentials")
        if is_callback:
            await update.callback_query.edit_message_text(message_text)
        else:
            await update.message.reply_text(message_text)
        return
    
    # Check if user has work schedule
    if 'work_schedule' not in user_credentials[str(user_id)]:
        message_text = "You haven't set your work schedule yet. Please use /work_schedule to set it up."
        logger.warning(f"User {user_id} has no work schedule")
        if is_callback:
            await update.callback_query.edit_message_text(message_text)
        else:
            await update.message.reply_text(message_text)
        return
    
    # Send initial message
    if is_callback:
        message = await update.callback_query.edit_message_text("Generating report... Please wait.")
    else:
        message = await update.message.reply_text("Generating report... Please wait.")
    
    # Create a temporary directory for files
    with tempfile.TemporaryDirectory() as temp_dir:
        # Prepare arguments for plot_times
        args = []
        
        if plot_month:
            args.append("-M")
            report_type = "monthly"
        elif plot_week:
            args.append("-W")
            report_type = "weekly"
        elif custom_month:
            args.extend(["-c", custom_month])
            report_type = f"custom ({custom_month})"
        else:
            # Default to current month
            args.append("-M")
            report_type = "monthly"
        
        # Capture output
        try:
            # Get user credentials
            creds = user_credentials[str(user_id)]
            logger.info(f"Using credentials for user {user_id}")
            
            # Set credentials for this session
            plot_times.SESSION_ID = creds['session_id']
            plot_times.CSRF_TOKEN = creds['csrf_token']
            plot_times.UID = creds['odoo_uid']
            
            # Update expected hours based on user's work schedule
            update_plot_times_expected_hours(user_id)
            
            # First, pull the attendance and leave data
            logger.info("Starting to pull attendance and leave data...")
            try:
                plot_times.pull_attendance_leave_lists()
                logger.info("Successfully pulled attendance and leave data")
            except requests.exceptions.HTTPError as e:
                logger.error(f"HTTP error when pulling data: {e}")
                error_message = "Failed to retrieve data from Odoo. "
                
                if e.response.status_code == 401 or e.response.status_code == 403:
                    error_message += "Your session may have expired or your credentials are invalid. Please update your credentials using /credentials command."
                elif e.response.status_code == 404:
                    error_message += "The requested resource was not found. Please check your Odoo URL."
                else:
                    error_message += f"Error code: {e.response.status_code}. Please try again later or update your credentials."
                
                await message.edit_text(error_message)
                await show_menu_buttons(update, context)
                return
            except Exception as e:
                logger.error(f"Error pulling attendance and leave data: {e}")
                await message.edit_text(f"Error retrieving data: {str(e)}\n\nPlease check your credentials and try again.")
                await show_menu_buttons(update, context)
                return
            
            # Determine file paths
            attendance_file = os.path.join(os.getcwd(), plot_times.ATTENDANCE_FILENAME)
            leave_file = os.path.join(os.getcwd(), plot_times.LEAVE_FILENAME)
            
            # Add file paths to args
            args.extend(["-af", attendance_file, "-lf", leave_file])
            logger.info(f"Arguments prepared: {args}")
            
            # Run the script and capture output
            if status_only:
                # For status, we'll run the script directly and capture output
                # Capture stdout
                import io
                import sys
                from contextlib import redirect_stdout
                
                logger.info("Running status report...")
                # Save original argv
                original_argv = sys.argv
                # Set sys.argv to our args
                sys.argv = ['plot_times.py'] + args
                
                f = io.StringIO()
                with redirect_stdout(f):
                    logger.info("Calling plot_times.main()...")
                    try:
                        run_with_timeout(plot_times.main, 120)  # 2 minute timeout
                        logger.info("plot_times.main() completed")
                    except TimeoutException:
                        logger.error("plot_times.main() timed out after 120 seconds")
                        raise TimeoutException("Report generation timed out after 120 seconds. Please try again later.")
                raw_output = f.getvalue()
                
                # Restore original argv
                sys.argv = original_argv
                
                # Format the output
                formatted_output = format_report_output(raw_output, user_id, weekly_report=plot_week)
                
                logger.info("Status report generated, sending to user")
                await message.edit_text(formatted_output, parse_mode="Markdown")
                
                # Show menu buttons after sending the report
                await show_menu_buttons(update, context)
                return
            
            # For reports with PDF, we'll run the script
            pdf_file = None
            
            # Determine the PDF filename based on the report type
            if plot_month:
                now = datetime.datetime.now()
                pdf_file = f"worktimes-{now.year}-{now.month}.pdf"
            elif plot_week:
                now = datetime.datetime.now()
                week_number = now.isocalendar().week
                pdf_file = f"worktimes-{now.year}-W{week_number}.pdf"
            elif custom_month:
                year, month = map(int, custom_month.split('-'))
                pdf_file = f"worktimes-{year}-{month:02d}.pdf"
            
            logger.info(f"Expected PDF file: {pdf_file}")
            
            # Run the script
            # Capture stdout
            import io
            import sys
            from contextlib import redirect_stdout
            
            logger.info("Running report generation...")
            # Save original argv
            original_argv = sys.argv
            # Set sys.argv to our args
            sys.argv = ['plot_times.py'] + args
            
            f = io.StringIO()
            with redirect_stdout(f):
                logger.info("Calling plot_times.main()...")
                try:
                    run_with_timeout(plot_times.main, 120)  # 2 minute timeout
                    logger.info("plot_times.main() completed")
                except TimeoutException:
                    logger.error("plot_times.main() timed out after 120 seconds")
                    raise TimeoutException("Report generation timed out after 120 seconds. Please try again later.")
            raw_output = f.getvalue()
            
            # Restore original argv
            sys.argv = original_argv
            
            # Format the output
            formatted_output = format_report_output(raw_output, user_id)
            
            logger.info(f"Checking if PDF file exists: {os.path.exists(pdf_file) if pdf_file else 'No PDF file specified'}")
            
            # Send the PDF if it exists
            if pdf_file and os.path.exists(pdf_file):
                logger.info(f"PDF file found, sending to user")
                with open(pdf_file, 'rb') as file:
                    await context.bot.send_document(
                        chat_id=update.effective_chat.id,
                        document=file,
                        filename=pdf_file,
                        caption=f"Time tracking report ({report_type})"
                    )
                
                # Send the text output as well
                await message.edit_text(formatted_output, parse_mode="Markdown")
                
                # Clean up
                os.remove(pdf_file)
                logger.info(f"PDF file sent and cleaned up")
            else:
                logger.info(f"No PDF file found, sending text output only")
                await message.edit_text(f"{formatted_output}\n\nNo PDF was generated.", parse_mode="Markdown")
            
            # Show menu buttons after sending the report
            await show_menu_buttons(update, context)
        
        except Exception as e:
            logger.error(f"Error generating report: {e}")
            logger.exception("Full traceback:")
            
            # Provide more specific error messages for common issues
            error_message = str(e)
            if "session ID or CSRF token" in error_message:
                await message.edit_text(
                    "Your session ID or CSRF token has expired or is invalid. Please update your credentials using the /credentials command."
                )
            elif "File is not a zip file" in error_message:
                await message.edit_text(
                    "Your session ID or CSRF token has expired or is invalid. Please update your credentials using the /credentials command."
                )
            else:
                await message.edit_text(f"Error generating report: {error_message}")
            
            # Show menu buttons even after error
            await show_menu_buttons(update, context)

# Add a new function to show menu buttons
async def show_menu_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the menu buttons to the user."""
    user_id = update.effective_user.id
    
    keyboard = [
        [
            InlineKeyboardButton("📅 Current Month", callback_data="month"),
            InlineKeyboardButton("📊 Current Week", callback_data="week"),
        ],
        [
            InlineKeyboardButton("🗓️ Custom Month", callback_data="custom_month"),
            InlineKeyboardButton("📈 Status", callback_data="status"),
        ],
        [
            InlineKeyboardButton("🔑 Set Credentials", callback_data="set_credentials"),
            InlineKeyboardButton("🔄 Auto Fetch Tokens", callback_data="auto_fetch_tokens"),
        ],
        [
            InlineKeyboardButton("⏰ Work Schedule", callback_data="work_schedule"),
            InlineKeyboardButton("❓ Help", callback_data="help"),
        ]
    ]
    
    # Check if user has credentials
    has_credentials = str(user_id) in user_credentials
    credential_status = "✅ Credentials set" if has_credentials else "❌ No credentials set"
    
    # Get formatted work schedule status
    work_schedule_status = get_formatted_work_schedule(user_id)
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Send a new message with the menu buttons
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"What would you like to do next?\n\n{credential_status}\n{work_schedule_status}",
        reply_markup=reply_markup
    )

def format_report_output(raw_output, user_id=None, weekly_report=False):
    """Format the raw output from plot_times.py into a cleaner, more organized format."""
    # Initialize formatted sections
    summary = ""
    holidays = ""
    leaves = ""
    weekly_hours = ""
    leave_summary = ""  # New section for leave summary
    
    # Process the raw output line by line to extract the relevant information
    lines = raw_output.split('\n')
    status = ""
    difference = ""
    
    # Extract the key metrics
    worked_hours_value = ""
    total_hours_value = ""
    hours_to_complete_value = ""
    
    # Scan through each line to find the metrics
    for line in lines:
        line = line.strip()
        if "Status:" in line and "Difference:" in line:
            parts = line.split(", Difference:")
            status = parts[0].replace("Status:", "").strip()
            difference = parts[1].strip()
        elif "Total work time accounted so far" in line:
            worked_hours_value = line.strip()
        elif "Total hours Accounted this period" in line:
            total_hours_value = line.strip()
        elif "Total hours To complete this period" in line:
            hours_to_complete_value = line.strip()
    
    # Format the summary section with emojis and clear labels
    summary = f"*📊 Time Tracking Summary*\n\n"
    
    # Add status with appropriate emoji and formatting
    if status.lower() == "overtime":
        summary += f"✅ *Status:* {status}\n"
    else:
        summary += f"⚠️ *Status:* {status}\n"
    
    summary += f"⏱️ *Difference:* {difference}\n\n"
    
    # Add the detailed metrics with better formatting
    if worked_hours_value:
        # Extract just the hours and minutes part
        import re
        hours_match = re.search(r"(\d+) hours and (\d+) minutes", worked_hours_value)
        if hours_match:
            hours = hours_match.group(1)
            minutes = hours_match.group(2)
            summary += f"🕒 *Actual Hours Worked:* {hours}h {minutes}m\n"
    
    if total_hours_value:
        # Format the total hours more cleanly
        import re
        # Look for patterns like "X hours and Y minutes of Z hours and W minutes"
        match = re.search(r"(\d+) hours and (\d+) minutes of (\d+) hours and (\d+) minutes", total_hours_value)
        if match:
            accounted_hours = match.group(1)
            accounted_minutes = match.group(2)
            expected_hours = match.group(3)
            expected_minutes = match.group(4)
            summary += f"📆 *Expected Hours:* {expected_hours}h {expected_minutes}m\n"
            summary += f"✓ *Total Hours Accounted:* {accounted_hours}h {accounted_minutes}m\n"
    
    if hours_to_complete_value:
        # Extract the remaining hours needed
        import re
        match = re.search(r"(\d+) hours and (\d+) minutes of", hours_to_complete_value)
        if match:
            remaining_hours = match.group(1)
            remaining_minutes = match.group(2)
            summary += f"⏳ *Remaining Hours Needed:* {remaining_hours}h {remaining_minutes}m\n"
    
    # Extract holidays information
    holidays_section = ""
    holidays_found = False
    holiday_hours = 0.0
    for i, line in enumerate(lines):
        if "List of Holidays:" in line:
            holidays_found = True
            j = i + 1
            while j < len(lines) and lines[j].strip() and not lines[j].startswith("List of"):
                # Format each holiday entry
                holiday_line = lines[j].strip()
                # Try to extract date and type
                date_match = re.search(r"Date: (.*?),", holiday_line)
                if date_match:
                    date = date_match.group(1)
                    # Extract hours if available
                    hours_match = re.search(r"Hours Accounted: (.*?)h", holiday_line)
                    if hours_match:
                        try:
                            holiday_hours += float(hours_match.group(1))
                        except ValueError:
                            pass
                    formatted_line = f"• *{date}*: " + holiday_line.split("Date: " + date + ",")[1].strip()
                    holidays_section += f"{formatted_line}\n"
                else:
                    holidays_section += f"• {holiday_line}\n"
                j += 1
            break
    
    if holidays_found and holidays_section.strip():
        holidays = f"\n*🏖️ Holidays*\n{holidays_section}"
    else:
        holidays = "\n*🏖️ Holidays*\n• No holidays in this period."
    
    # Extract leaves information
    leaves_section = ""
    leaves_found = False
    sick_leave_hours = 0.0
    vacation_hours = 0.0
    half_day_hours = 0.0
    other_leave_hours = 0.0
    
    for i, line in enumerate(lines):
        if "List of Leaves and Half Days:" in line:
            leaves_found = True
            j = i + 1
            while j < len(lines) and lines[j].strip() and not lines[j].startswith("List of") and not lines[j].startswith("Total weekly"):
                # Format each leave entry
                leave_line = lines[j].strip()
                # Try to extract date and type
                date_match = re.search(r"Date: (.*?),", leave_line)
                if date_match:
                    date = date_match.group(1)
                    type_match = re.search(r"Type: (.*?),", leave_line)
                    if type_match:
                        leave_type = type_match.group(1)
                        hours_match = re.search(r"Hours Accounted: (.*?)h", leave_line)
                        hours = hours_match.group(1) if hours_match else "0.0"
                        
                        # Track hours by leave type
                        try:
                            hours_float = float(hours)
                            if "Half Day" in leave_type:
                                half_day_hours += hours_float
                            elif "Krankheit" in leave_type or "Sick" in leave_type:
                                sick_leave_hours += hours_float
                            elif "Urlaub" in leave_type or "Vacation" in leave_type:
                                vacation_hours += hours_float
                            else:
                                other_leave_hours += hours_float
                        except ValueError:
                            pass
                            
                        leaves_section += f"• *{date}* - {leave_type} ({hours}h)\n"
                    else:
                        formatted_line = f"• *{date}*: " + leave_line.split("Date: " + date + ",")[1].strip()
                        leaves_section += f"{formatted_line}\n"
                else:
                    leaves_section += f"• {leave_line}\n"
                j += 1
            break
    
    if leaves_found and leaves_section.strip():
        leaves = f"\n*🌴 Leaves & Half Days*\n{leaves_section}"
    else:
        leaves = "\n*🌴 Leaves & Half Days*\n• No leaves or half days in this period."
    
    # Create the leave summary section
    total_leave_hours = sick_leave_hours + vacation_hours + half_day_hours + holiday_hours + other_leave_hours
    
    leave_summary = "\n*📝 Leave Hours Summary*\n"
    if sick_leave_hours > 0:
        leave_summary += f"• 🤒 *Sick Leave:* {sick_leave_hours:.1f}h\n"
    if vacation_hours > 0:
        leave_summary += f"• 🏝️ *Vacation:* {vacation_hours:.1f}h\n"
    if half_day_hours > 0:
        leave_summary += f"• 🕛 *Half Days:* {half_day_hours:.1f}h\n"
    if holiday_hours > 0:
        leave_summary += f"• 🎉 *Holidays:* {holiday_hours:.1f}h\n"
    if other_leave_hours > 0:
        leave_summary += f"• 📅 *Other Leaves:* {other_leave_hours:.1f}h\n"
    
    if total_leave_hours > 0:
        leave_summary += f"• 🔖 *Total Leave Hours:* {total_leave_hours:.1f}h\n"
    else:
        leave_summary += "• No leave hours in this period.\n"
    
    # Extract weekly hours
    weekly_hours_section = ""
    weekly_found = False
    for i, line in enumerate(lines):
        if "Total weekly working hours:" in line:
            weekly_found = True
            j = i + 1
            while j < len(lines) and lines[j].strip() and "Week ending" in lines[j]:
                weekly_hours_section += f"{lines[j].strip()}\n"
                j += 1
            break
    
    if weekly_found and weekly_hours_section.strip():
        # Format the weekly hours to match the desired output
        formatted_weekly_hours = ""
        for line in weekly_hours_section.split('\n'):
            if line.strip():
                # Extract week ending date and hours
                import re
                week_match = re.search(r"Week ending (.*?): (.*?) hours and (.*?) minutes", line)
                if week_match:
                    date = week_match.group(1)
                    hours = week_match.group(2)
                    minutes = week_match.group(3)
                    # Format the date to be more readable
                    try:
                        date_obj = datetime.datetime.strptime(date, "%Y-%m-%d")
                        formatted_date = date_obj.strftime("%b %d")  # e.g., "Feb 09"
                        
                        # Use a bullet point instead of colored emoji
                        formatted_weekly_hours += f"• *Week ending {formatted_date}:* {hours}h {minutes}m\n"
                    except:
                        formatted_weekly_hours += f"• *Week ending {date}:* {hours}h {minutes}m\n"
                else:
                    formatted_weekly_hours += f"• {line}\n"
        
        weekly_hours = f"\n*📅 Weekly Hours*\n{formatted_weekly_hours}"
    else:
        weekly_hours = "\n*📅 Weekly Hours*\n• No weekly hours data available."
    
    # Add a progress bar for visual representation of completion
    if total_hours_value and worked_hours_value and not weekly_report:  # Only show progress for non-weekly reports
        import re
        # Extract expected and accounted hours
        expected_match = re.search(r"of (\d+) hours", total_hours_value)
        accounted_match = re.search(r"(\d+) hours and \d+ minutes of", total_hours_value)
        
        # Always show a progress bar even if we can't extract both values
        expected_hours = int(expected_match.group(1)) if expected_match else 0
        accounted_hours = int(accounted_match.group(1)) if accounted_match else 0
        
        # Calculate percentage
        if expected_hours <= 0 and accounted_hours > 0:
            percentage = 100
        elif expected_hours > 0:
            percentage = min(100, int((accounted_hours / expected_hours) * 100))
        else:
            percentage = 0
        
        # Create a progress bar with fixed-width Unicode block characters
        filled = int(percentage / 10)
        progress_bar = "█" * filled + "░" * (10 - filled)
        
        # Add to summary with appropriate emoji based on percentage
        # More granular color coding based on progress percentage
        if percentage >= 100:
            progress_emoji = "🟢"  # Green for complete/overtime (100%+)
        elif percentage >= 90:
            progress_emoji = "🟢"  # Green for almost complete (90-99%)
        elif percentage >= 80:
            progress_emoji = "🟡"  # Yellow for good progress (80-89%)
        elif percentage >= 60:
            progress_emoji = "🟠"  # Orange for medium progress (60-79%)
        elif percentage >= 30:
            progress_emoji = "🟣"  # Purple for low progress (30-59%)
        else:
            progress_emoji = "🔴"  # Red for very low progress (<30%)
        
        summary += f"\n{progress_emoji} *Progress:* {progress_bar} {percentage}%\n"
    else:
        summary += "\n"
    
    # Add a divider for better visual separation
    divider = "\n" + "•───────────────────────•" + "\n"
    
    # Add a header with current date
    current_date = datetime.datetime.now().strftime("%d %B %Y")
    header = f"*📋 Time Tracking Report - {current_date}*\n\n"
    
    # Add copyright footer with hyperlink
    copyright_footer = "\n_Generated by Odoo Time Tracking Bot_"
    
    # Combine all sections with dividers
    formatted_output = f"{header}{summary}{divider}{leave_summary}{divider}{holidays}{divider}{leaves}{divider}{weekly_hours}{copyright_footer}"
    
    return formatted_output

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle errors in the telegram bot."""
    error = context.error
    
    # Check if it's a message not modified error
    if str(error).startswith("Message is not modified"):
        # Just log it and ignore - this is not a real error
        logger.info("Attempted to edit message with same content - ignoring")
        return
    
    logger.error("Exception while handling an update:", exc_info=error)
    
    # Extract the Update and user info if possible
    if update and isinstance(update, Update) and update.effective_user:
        user_id = update.effective_user.id
        logger.error(f"Update from user {user_id} caused error: {error}")
        
        # Send message to the user
        if update.effective_message:
            error_message = f"❌ An error occurred: {str(error)}\n\nPlease try again or contact the administrator."
            await update.effective_message.reply_text(error_message)
            
            # Try to show menu buttons even after error
            try:
                await show_menu_buttons(update, context)
            except Exception as e:
                logger.error(f"Failed to show menu buttons after error: {e}")
    else:
        logger.error(f"Update {update} caused error {error}")

def main() -> None:
    """Start the bot."""
    # Create the Application and pass it your bot's token.
    application = Application.builder().token(TOKEN).build()

    # Define conversation states
    global WAITING_FOR_SESSION_ID, WAITING_FOR_CSRF_TOKEN, WAITING_FOR_ODOO_UID, WAITING_FOR_CUSTOM_MONTH, WAITING_FOR_ALL_HOURS_INPUT
    global CHOOSING_WORK_SCHEDULE, SETTING_WORK_DAYS, SETTING_WORK_HOURS, SETTING_SPECIFIC_HOURS, WAITING_FOR_HOURS_INPUT
    global CHOOSING_ACTION, WAITING_FOR_EMAIL, WAITING_FOR_PASSWORD
    
    CHOOSING_ACTION = 0
    WAITING_FOR_SESSION_ID = 1
    WAITING_FOR_CSRF_TOKEN = 2
    WAITING_FOR_ODOO_UID = 3
    WAITING_FOR_CUSTOM_MONTH = 4
    WAITING_FOR_ALL_HOURS_INPUT = 5
    CHOOSING_WORK_SCHEDULE = 6
    SETTING_WORK_DAYS = 7
    SETTING_WORK_HOURS = 8
    SETTING_SPECIFIC_HOURS = 9
    WAITING_FOR_HOURS_INPUT = 10
    WAITING_FOR_EMAIL = 11
    WAITING_FOR_PASSWORD = 12
    
    # Add conversation handler
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("credentials", credentials_command),
            CommandHandler("work_schedule", work_schedule_command),
            CommandHandler("custom", custom_command_entry),
            # Add CallbackQueryHandler to handle button presses from outside the conversation
            CallbackQueryHandler(button_callback),
        ],
        states={
            CHOOSING_ACTION: [
                CallbackQueryHandler(button_callback),
                MessageHandler(filters.TEXT & ~filters.COMMAND, lambda update, context: start(update, context)),
            ],
            WAITING_FOR_SESSION_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, session_id_input),
                # Add a command handler for /start to allow users to exit credential setting
                CommandHandler("start", start),
            ],
            WAITING_FOR_CSRF_TOKEN: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, csrf_token_input),
                CommandHandler("start", start),
            ],
            WAITING_FOR_ODOO_UID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, odoo_uid_input),
                CommandHandler("start", start),
            ],
            WAITING_FOR_CUSTOM_MONTH: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, custom_month_input),
                CallbackQueryHandler(button_callback),
                # Add a command handler for /start to allow users to exit the custom month state
                CommandHandler("start", start),
            ],
            WAITING_FOR_ALL_HOURS_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, all_hours_input),
                CommandHandler("start", start),
            ],
            CHOOSING_WORK_SCHEDULE: [
                CallbackQueryHandler(button_callback),
                CommandHandler("start", start),
            ],
            SETTING_WORK_DAYS: [
                CallbackQueryHandler(button_callback),
                CommandHandler("start", start),
            ],
            SETTING_WORK_HOURS: [
                CallbackQueryHandler(button_callback),
                CommandHandler("start", start),
            ],
            SETTING_SPECIFIC_HOURS: [
                CallbackQueryHandler(button_callback),
                CommandHandler("start", start),
            ],
            WAITING_FOR_HOURS_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, hours_input),
                CommandHandler("start", start),
            ],
            WAITING_FOR_EMAIL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, email_input),
                CommandHandler("start", start),
            ],
            WAITING_FOR_PASSWORD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, password_input),
                CommandHandler("start", start),
            ],
        },
        fallbacks=[
            CommandHandler("start", start),
            CommandHandler("help", help_command),
            CommandHandler("month", month_command),
            CommandHandler("week", week_command),
            CommandHandler("status", status_command),
            CommandHandler("work_schedule", work_schedule_command),
            # Add CallbackQueryHandler to handle button presses in fallback
            CallbackQueryHandler(button_callback),
        ],
        name="main_conversation",
        per_message=False,  # Change this to False to allow command handlers to work
        # Add a conversation timeout to prevent the conversation from getting stuck
        conversation_timeout=300,  # 5 minutes timeout
    )

    # Add conversation handler
    application.add_handler(conv_handler)
    
    # Add command handlers
    application.add_handler(CommandHandler("month", month_command))
    application.add_handler(CommandHandler("week", week_command))
    application.add_handler(CommandHandler("custom", custom_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("debug", debug_command))
    application.add_handler(CommandHandler("work_schedule", work_schedule_command))
    
    # Add error handler
    application.add_error_handler(error_handler)
    
    # Start the Bot
    application.run_polling()

async def custom_command_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point for the /custom command to set up the conversation state."""
    user_id = update.effective_user.id
    logger.info(f"User {user_id} used /custom command")
    
    if context.args:
        # If arguments are provided, use them directly
        custom_month = context.args[0]
        try:
            year, month = map(int, custom_month.split('-'))
            if not (1 <= month <= 12):
                raise ValueError
            # Valid format, generate report directly
            await generate_report(update, context, custom_month=custom_month)
            return ConversationHandler.END
        except ValueError:
            # Invalid format, prompt for correct format
            await update.message.reply_text("Invalid format. Please use YYYY-MM (e.g., 2024-12)")
            # Fall through to prompt for input
    
    # Store the fact that we're waiting for custom month input
    context.user_data['awaiting_custom_month'] = True
    
    # Prompt for custom month input
    await update.message.reply_text(
        "Please send the month in format YYYY-MM (e.g., 2024-12) or just the month number (1-12)"
    )
    return WAITING_FOR_CUSTOM_MONTH

async def show_work_schedule_options(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show work schedule options."""
    query = update.callback_query
    user_id = query.from_user.id
    
    # Check if user has credentials
    if str(user_id) not in user_credentials:
        await query.edit_message_text(
            "You need to set your credentials first before setting your work schedule. Use /credentials to set them up."
        )
        await show_menu_buttons(update, context)
        return ConversationHandler.END
    
    keyboard = [
        [InlineKeyboardButton("⏰ Full Time (40h/week)", callback_data="hours_full_time")],
        [InlineKeyboardButton("⌛ Part Time", callback_data="part_time_custom")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_work_schedule")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Show current work schedule if it exists
    current_schedule = ""
    if 'work_schedule' in user_credentials[str(user_id)]:
        work_schedule = user_credentials[str(user_id)]['work_schedule']
        schedule_type = work_schedule.get('type', 'custom')
        
        if schedule_type == FULL_TIME:
            current_schedule = "Current: Full Time (40h/week)"
        elif schedule_type == PART_TIME:
            current_schedule = "Current: Part Time (20h/week)"
        else:
            # Custom schedule - show days and hours
            work_days = work_schedule.get('days', {})
            work_hours = work_schedule.get('hours', {})
            
            days_text = ", ".join([day for day, enabled in work_days.items() if enabled])
            total_hours = sum([hours for day, hours in work_hours.items() if work_days.get(day, False)])
            
            current_schedule = f"Current: Custom ({total_hours}h/week on {days_text})"
    
    await query.edit_message_text(
        f"Please select your work schedule type:\n\n{current_schedule}",
        reply_markup=reply_markup
    )
    
    return CHOOSING_WORK_SCHEDULE

async def set_work_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE, schedule_type: str) -> int:
    """Set work schedule based on predefined types."""
    query = update.callback_query
    user_id = query.from_user.id
    
    # Create work schedule based on type
    if schedule_type == FULL_TIME:
        work_days = {
            "Mon": True,
            "Tue": True,
            "Wed": True,
            "Thu": True,
            "Fri": True,
            "Sat": False,
            "Sun": False
        }
        work_hours = {
            "Mon": 8.0,
            "Tue": 8.0,
            "Wed": 8.0,
            "Thu": 8.0,
            "Fri": 8.0,
            "Sat": 0.0,
            "Sun": 0.0
        }
        schedule_name = "Full Time (40h/week)"
    elif schedule_type == PART_TIME:
        work_days = {
            "Mon": True,
            "Tue": True,
            "Wed": True,
            "Thu": True,
            "Fri": True,
            "Sat": False,
            "Sun": False
        }
        # Distribute 20 hours across 5 weekdays (4 hours per day)
        work_hours = {
            "Mon": 4.0,
            "Tue": 4.0,
            "Wed": 4.0,
            "Thu": 4.0,
            "Fri": 4.0,
            "Sat": 0.0,
            "Sun": 0.0
        }
        schedule_name = "Part Time"
    else:
        # Should not happen, but just in case
        await query.edit_message_text("Invalid schedule type. Please try again.")
        await show_menu_buttons(update, context)
        return ConversationHandler.END
    
    # Save work schedule to user credentials
    global user_credentials
    user_credentials[str(user_id)]['work_schedule'] = {
        'type': schedule_type,
        'days': work_days,
        'hours': work_hours
    }
    
    # Save to environment variable
    save_credentials(user_credentials)
    
    # Update plot_times expected hours
    update_plot_times_expected_hours(user_id)
    
    await query.edit_message_text(f"Your work schedule has been set to {schedule_name}.")
    await show_menu_buttons(update, context)
    
    return ConversationHandler.END

async def start_custom_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start the process of setting a custom work schedule."""
    query = update.callback_query
    user_id = query.from_user.id
    
    # Initialize with default or existing work days
    if str(user_id) in user_credentials and 'work_schedule' in user_credentials[str(user_id)]:
        work_days = user_credentials[str(user_id)]['work_schedule'].get('days', DEFAULT_WORK_DAYS.copy())
        work_hours = user_credentials[str(user_id)]['work_schedule'].get('hours', {})
    else:
        work_days = DEFAULT_WORK_DAYS.copy()
        work_hours = {}
    
    # Store in context for the conversation
    context.user_data['temp_work_days'] = work_days
    context.user_data['temp_work_hours'] = work_hours
    context.user_data['is_custom_schedule'] = True
    
    # Show work days selection
    return await show_work_days_selection(update, context)

async def show_work_days_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show the work days selection interface."""
    query = update.callback_query
    
    # Get the temporary work days from context
    work_days = context.user_data.get('temp_work_days', DEFAULT_WORK_DAYS.copy())
    
    # Create keyboard with toggles for each day (only weekdays)
    keyboard = []
    for day in ["Mon", "Tue", "Wed", "Thu", "Fri"]:  # Only weekdays
        status = "✅" if work_days.get(day, False) else "❌"
        keyboard.append([
            InlineKeyboardButton(f"{day}: {status}", callback_data=f"toggle_day_{day}")
        ])
    
    # Add save and cancel buttons
    keyboard.append([
        InlineKeyboardButton("✅ Continue to Set Hours", callback_data="save_work_days"),
        InlineKeyboardButton("❌ Cancel", callback_data="cancel_work_schedule")
    ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Count selected days (only weekdays)
    selected_days = sum(1 for day, enabled in work_days.items() 
                       if day in ["Mon", "Tue", "Wed", "Thu", "Fri"] and enabled)
    
    await query.edit_message_text(
        f"Select your work days:\n"
        f"(Click on a day to toggle it on/off)\n\n"
        f"Selected days: {selected_days}/5\n"
        f"After selecting days, you'll be able to set specific hours for each day.",
        reply_markup=reply_markup
    )
    
    return SETTING_WORK_DAYS

async def toggle_work_day(update: Update, context: ContextTypes.DEFAULT_TYPE, day: str) -> int:
    """Toggle a work day on or off."""
    # Get the temporary work days from context
    work_days = context.user_data.get('temp_work_days', DEFAULT_WORK_DAYS.copy())
    
    # Toggle the selected day
    work_days[day] = not work_days.get(day, False)
    
    # Update the context
    context.user_data['temp_work_days'] = work_days
    
    # Show the updated selection
    return await show_work_days_selection(update, context)

async def save_work_days(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save the selected work days and proceed to hours setup."""
    query = update.callback_query
    user_id = query.from_user.id
    
    # Get the current work days from context
    work_days = context.user_data.get('temp_work_days', {})
    
    # Count enabled work days
    enabled_days = [day for day, enabled in work_days.items() if enabled]
    if not enabled_days:
        await query.edit_message_text(
            "You must select at least one work day. Please try again."
        )
        return await show_work_days_selection(update, context)
    
    # Store work days in context for later use
    context.user_data['work_days'] = work_days
    
    # Format days for display using full names and proper sorting
    day_order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    day_full_names = {
        "Mon": "Monday",
        "Tue": "Tuesday",
        "Wed": "Wednesday",
        "Thu": "Thursday",
        "Fri": "Friday",
        "Sat": "Saturday",
        "Sun": "Sunday"
    }
    
    # Create reverse mapping from full names to abbreviations
    full_to_abbrev = {v: k for k, v in day_full_names.items()}
    
    # Convert any full day names to abbreviations before sorting
    normalized_enabled_days = []
    for day in enabled_days:
        if day in full_to_abbrev:  # If it's a full day name
            normalized_enabled_days.append(full_to_abbrev[day])
        else:  # It's already an abbreviation
            normalized_enabled_days.append(day)
    
    # Sort days according to the week order
    sorted_enabled_days = sorted(normalized_enabled_days, key=lambda x: day_order.index(x))
    formatted_days = [day_full_names[day] for day in sorted_enabled_days]
    days_text = ", ".join(formatted_days)
    
    # Calculate total hours per week (default to 20 hours per week for part-time)
    total_hours_per_week = 20.0
    hours_per_day = round(total_hours_per_week / len(enabled_days), 1)
    
    # Store the calculated hours in context
    context.user_data['hours_per_day'] = hours_per_day
    
    # Create initial hours distribution
    work_hours = {day: hours_per_day if enabled else 0.0 for day, enabled in work_days.items()}
    context.user_data['work_hours'] = work_hours
    
    await query.edit_message_text(
        f"Your work schedule has been saved.\n\n"
        f"Work days: {days_text}\n"
        f"Total hours per week: {total_hours_per_week}h\n"
        f"Hours per day: {hours_per_day}h\n\n"
        f"How would you like to set up your work hours?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⌛ Standard Distribution", callback_data="hours_standard")],
            [InlineKeyboardButton("📝 Set Specific Hours per Day", callback_data="set_specific_hours")],
            [InlineKeyboardButton("◀️ Back to Day Selection", callback_data="back_to_days")],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_work_schedule")]
        ])
    )
    
    return SETTING_WORK_HOURS

async def set_hours_distribution(update: Update, context: ContextTypes.DEFAULT_TYPE, hours_type: str) -> int:
    """Set the distribution of hours based on the selected type."""
    query = update.callback_query
    user_id = query.from_user.id
    
    # Get the temporary work days from context
    work_days = context.user_data.get('temp_work_days', DEFAULT_WORK_DAYS.copy())
    
    # Calculate selected days count
    selected_days = [day for day, enabled in work_days.items() if enabled]
    selected_days_count = len(selected_days)
    
    if selected_days_count == 0:
        await query.edit_message_text(
            "No work days selected. Please go back and select at least one work day."
        )
        return await show_work_days_selection(update, context)
    
    # Determine total hours based on the selected type
    if hours_type == "hours_full_time":
        total_hours = 40.0
        schedule_type = FULL_TIME
    elif hours_type == "hours_part_time":
        total_hours = 20.0
        schedule_type = PART_TIME
    else:
        # Default to custom
        total_hours = float(context.user_data.get('total_hours', 20.0))
        schedule_type = "custom"
    
    # Distribute hours evenly across selected days
    hours_per_day = total_hours / selected_days_count if selected_days_count > 0 else 0
    
    # Create hours dictionary
    work_hours = {}
    for day in WEEKDAY_KEYS:
        work_hours[day] = hours_per_day if work_days.get(day, False) else 0.0
    
    # Save to user credentials
    global user_credentials
    if str(user_id) not in user_credentials:
        user_credentials[str(user_id)] = {}
    
    user_credentials[str(user_id)]['work_schedule'] = {
        'type': schedule_type,
        'days': work_days,
        'hours': work_hours
    }
    
    # Save to environment variable
    save_credentials(user_credentials)
    
    # Update plot_times expected hours
    update_plot_times_expected_hours(user_id)
    
    # Show confirmation
    days_text = ", ".join([day for day, enabled in work_days.items() if enabled])
    await query.edit_message_text(
        f"Your work schedule has been saved.\n\n"
        f"Work days: {days_text}\n"
        f"Total hours per week: {total_hours:.1f}h\n"
        f"Hours per day: {hours_per_day:.1f}h"
    )
    
    await show_menu_buttons(update, context)
    return ConversationHandler.END

async def show_specific_hours_setup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show options for setting up specific hours for each work day."""
    query = update.callback_query
    user_id = query.from_user.id
    
    work_days = context.user_data.get('temp_work_days', {})
    work_hours = context.user_data.get('temp_work_hours', {})
    
    # Only show buttons for enabled work days
    keyboard = []
    day_order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    day_full_names = {
        "Mon": "Monday",
        "Tue": "Tuesday",
        "Wed": "Wednesday",
        "Thu": "Thursday",
        "Fri": "Friday",
        "Sat": "Saturday",
        "Sun": "Sunday"
    }
    
    # Sort days according to week order and only include enabled days
    enabled_days = sorted(
        [day for day, enabled in work_days.items() if enabled],
        key=lambda x: day_order.index(x)
    )
    
    # Create buttons for each enabled day
    for day in enabled_days:
        hours = work_hours.get(day, 0.0)
        keyboard.append([
            InlineKeyboardButton(
                f"{day_full_names[day]}: {hours}h",
                callback_data=f"edit_hours_{day}"
            )
        ])
    
    # Add control buttons
    keyboard.extend([
        [InlineKeyboardButton("💼 Set All Hours at Once", callback_data="set_all_hours")],
        [InlineKeyboardButton("✅ Save Hours", callback_data="save_specific_hours")],
        [InlineKeyboardButton("◀️ Back to Day Selection", callback_data="back_to_days")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_work_schedule")]
    ])
    
    total_hours = sum(work_hours.values())
    formatted_days = [day_full_names[day] for day in enabled_days]
    days_text = ", ".join(formatted_days)
    
    await query.edit_message_text(
        f"Set your work hours for each day:\n\n"
        f"Work days: {days_text}\n"
        f"Total hours per week: {total_hours:.1f}h\n\n"
        f"Click on a day to edit its hours:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    
    return SETTING_SPECIFIC_HOURS

async def set_all_hours_at_once(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show interface to set all hours at once."""
    query = update.callback_query
    await query.answer()
    
    # Get the temporary work days from context
    work_days = context.user_data.get('temp_work_days', DEFAULT_WORK_DAYS.copy())
    work_hours = context.user_data.get('temp_work_hours', {})
    
    # Create a message showing current hours for each selected day
    message = "Enter hours for each work day in a single message.\n\n"
    message += "Format: Use one line per day with 'Day: hours'\n"
    message += "Example:\n"
    
    # Add example and current values
    selected_days = []
    for day in WEEKDAY_KEYS:
        if work_days.get(day, False):
            selected_days.append(day)
            current_hours = work_hours.get(day, 0.0)
            message += f"{day}: {current_hours:.1f}\n"
    
    message += "\nEnter your hours below:"
    
    # Store selected days for validation
    context.user_data['selected_days'] = selected_days
    
    await query.edit_message_text(message)
    
    return WAITING_FOR_ALL_HOURS_INPUT

async def all_hours_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle user input for all hours at once."""
    user_input = update.message.text.strip()
    user_id = update.message.from_user.id
    
    # Get selected days
    selected_days = context.user_data.get('selected_days', [])
    work_hours = context.user_data.get('temp_work_hours', {})
    
    # Parse the input
    lines = user_input.strip().split('\n')
    parsed_hours = {}
    errors = []
    
    for line in lines:
        if not line.strip():
            continue
            
        parts = line.split(':')
        if len(parts) != 2:
            errors.append(f"Invalid format in line: '{line}'. Use 'Day: hours'")
            continue
            
        day = parts[0].strip()
        if day not in WEEKDAY_KEYS:
            errors.append(f"Invalid day '{day}'. Use one of {', '.join(WEEKDAY_KEYS)}")
            continue
            
        if day not in selected_days:
            errors.append(f"Day '{day}' is not selected as a work day")
            continue
            
        try:
            hours = float(parts[1].strip())
            if hours < 0 or hours > 12:
                errors.append(f"Hours for {day} must be between 0 and 12")
                continue
                
            parsed_hours[day] = hours
        except ValueError:
            errors.append(f"Invalid hours value for {day}: '{parts[1].strip()}'")
    
    # Check if all selected days have hours
    for day in selected_days:
        if day not in parsed_hours:
            errors.append(f"Missing hours for {day}")
    
    # If there are errors, show them and ask again
    if errors:
        error_message = "There were errors in your input:\n\n"
        error_message += "\n".join(errors)
        error_message += "\n\nPlease try again."
        await update.message.reply_text(error_message)
        return WAITING_FOR_ALL_HOURS_INPUT
    
    # Update hours
    for day, hours in parsed_hours.items():
        work_hours[day] = hours
    
    context.user_data['temp_work_hours'] = work_hours
    
    # Calculate total hours
    total_hours = sum(work_hours.get(day, 0.0) for day in WEEKDAY_KEYS if day in selected_days)
    
    # Confirm the hours were set
    await update.message.reply_text(
        f"Hours updated successfully!\n\n"
        f"Total hours per week: {total_hours:.1f}h"
    )
    
    # Instead of using a fake callback query, create a new message with the hours setup
    work_days = context.user_data.get('temp_work_days', {})
    
    # Only show buttons for enabled work days
    keyboard = []
    day_order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    day_full_names = {
        "Mon": "Monday",
        "Tue": "Tuesday",
        "Wed": "Wednesday",
        "Thu": "Thursday",
        "Fri": "Friday",
        "Sat": "Saturday",
        "Sun": "Sunday"
    }
    
    # Sort days according to week order and only include enabled days
    enabled_days = sorted(
        [day for day, enabled in work_days.items() if enabled],
        key=lambda x: day_order.index(x)
    )
    
    # Create buttons for each enabled day
    for day in enabled_days:
        hours = work_hours.get(day, 0.0)
        keyboard.append([
            InlineKeyboardButton(
                f"{day_full_names[day]}: {hours}h",
                callback_data=f"edit_hours_{day}"
            )
        ])
    
    # Add control buttons
    keyboard.extend([
        [InlineKeyboardButton("💼 Set All Hours at Once", callback_data="set_all_hours")],
        [InlineKeyboardButton("✅ Save Hours", callback_data="save_specific_hours")],
        [InlineKeyboardButton("◀️ Back to Day Selection", callback_data="back_to_days")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_work_schedule")]
    ])
    
    total_hours = sum(work_hours.values())
    formatted_days = [day_full_names[day] for day in enabled_days]
    days_text = ", ".join(formatted_days)
    
    await update.message.reply_text(
        f"Set your work hours for each day:\n\n"
        f"Work days: {days_text}\n"
        f"Total hours per week: {total_hours:.1f}h\n\n"
        f"Click on a day to edit its hours:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    
    return SETTING_SPECIFIC_HOURS

async def edit_day_hours(update: Update, context: ContextTypes.DEFAULT_TYPE, day: str) -> int:
    """Edit the hours for a specific day."""
    query = update.callback_query
    
    # Store the day being edited
    context.user_data['editing_day'] = day
    
    # Get current hours for this day
    work_hours = context.user_data.get('temp_work_hours', {})
    current_hours = work_hours.get(day, 0.0)
    
    await query.edit_message_text(
        f"Enter the number of hours for {day}:\n"
        f"(Current: {current_hours:.1f}h)\n\n"
        f"Please enter a number between 0 and 12, e.g., 8 or 7.5"
    )
    
    return WAITING_FOR_HOURS_INPUT

# Define FakeCallbackQuery class for use in hours_input
class FakeCallbackQuery:
    def __init__(self, user_id):
        self.from_user = type('obj', (object,), {'id': user_id})
        self.data = "work_schedule"
    
    async def edit_message_text(self, text, reply_markup=None):
        pass
    
    async def answer(self):
        pass

async def hours_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle user input for specific hours."""
    user_input = update.message.text.strip()
    user_id = update.message.from_user.id
    
    try:
        hours = float(user_input)
        if hours < 0 or hours > 12:
            await update.message.reply_text(
                "Please enter a valid number between 0 and 12."
            )
            return WAITING_FOR_HOURS_INPUT
        
        # Get the day being edited
        day = context.user_data.get('editing_day')
        if not day:
            await update.message.reply_text(
                "Error: No day selected for editing. Please try again."
            )
            # Create a new update with a fake callback query
            fake_query = FakeCallbackQuery(user_id)
            # Important: Log this operation
            logger.info(f"Creating fake callback query for user {user_id} to return to hours setup")
            # Create a new update object with the callback_query attribute
            fake_update = Update(update.update_id, callback_query=fake_query)
            return await show_specific_hours_setup(fake_update, context)
        
        # Update hours for this day
        work_hours = context.user_data.get('temp_work_hours', {})
        work_hours[day] = hours
        context.user_data['temp_work_hours'] = work_hours
        
        # Confirm the hours were set
        await update.message.reply_text(
            f"Hours for {day} set to {hours:.1f}h"
        )
        
        # Instead of using a fake callback query, create a new message with the hours setup
        work_days = context.user_data.get('temp_work_days', {})
        
        # Only show buttons for enabled work days
        keyboard = []
        day_order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        day_full_names = {
            "Mon": "Monday",
            "Tue": "Tuesday",
            "Wed": "Wednesday",
            "Thu": "Thursday",
            "Fri": "Friday",
            "Sat": "Saturday",
            "Sun": "Sunday"
        }
        
        # Sort days according to week order and only include enabled days
        enabled_days = sorted(
            [day for day, enabled in work_days.items() if enabled],
            key=lambda x: day_order.index(x)
        )
        
        # Create buttons for each enabled day
        for day in enabled_days:
            hours = work_hours.get(day, 0.0)
            keyboard.append([
                InlineKeyboardButton(
                    f"{day_full_names[day]}: {hours}h",
                    callback_data=f"edit_hours_{day}"
                )
            ])
        
        # Add control buttons
        keyboard.extend([
            [InlineKeyboardButton("💼 Set All Hours at Once", callback_data="set_all_hours")],
            [InlineKeyboardButton("✅ Save Hours", callback_data="save_specific_hours")],
            [InlineKeyboardButton("◀️ Back to Day Selection", callback_data="back_to_days")],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_work_schedule")]
        ])
        
        total_hours = sum(work_hours.values())
        formatted_days = [day_full_names[day] for day in enabled_days]
        days_text = ", ".join(formatted_days)
        
        await update.message.reply_text(
            f"Set your work hours for each day:\n\n"
            f"Work days: {days_text}\n"
            f"Total hours per week: {total_hours:.1f}h\n\n"
            f"Click on a day to edit its hours:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
        return SETTING_SPECIFIC_HOURS
        
    except ValueError:
        await update.message.reply_text(
            "Please enter a valid number (e.g., 8 or 7.5)."
        )
        return WAITING_FOR_HOURS_INPUT

async def save_specific_hours(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save the specific hours setup and complete the work schedule setup."""
    query = update.callback_query
    user_id = query.from_user.id
    
    work_days = context.user_data.get('temp_work_days', {})
    work_hours = context.user_data.get('temp_work_hours', {})
    
    # Validate the hours
    total_hours = sum(work_hours.values())
    if total_hours == 0:
        await query.edit_message_text(
            "Total work hours cannot be zero. Please set your work hours."
        )
        return await show_specific_hours_setup(update, context)
    
    # Save the temporary work days and hours to the permanent storage
    context.user_data['work_days'] = work_days
    context.user_data['work_hours'] = work_hours
    
    # Format days for display
    day_order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    day_full_names = {
        "Mon": "Monday",
        "Tue": "Tuesday",
        "Wed": "Wednesday",
        "Thu": "Thursday",
        "Fri": "Friday",
        "Sat": "Saturday",
        "Sun": "Sunday"
    }
    
    # Create reverse mapping from full names to abbreviations
    full_to_abbrev = {v: k for k, v in day_full_names.items()}
    
    # Convert any full day names to abbreviations before sorting
    normalized_enabled_days = []
    for day in [day for day, enabled in work_days.items() if enabled]:
        if day in full_to_abbrev:  # If it's a full day name
            normalized_enabled_days.append(full_to_abbrev[day])
        else:  # It's already an abbreviation
            normalized_enabled_days.append(day)
    
    # Sort and format enabled days
    enabled_days = sorted(
        normalized_enabled_days,
        key=lambda x: day_order.index(x)
    )
    formatted_days = [day_full_names[day] for day in enabled_days]
    days_text = ", ".join(formatted_days)
    
    # Save to user credentials
    if str(user_id) not in user_credentials:
        user_credentials[str(user_id)] = {}
    
    user_credentials[str(user_id)]['work_schedule'] = {
        'type': 'custom',
        'days': work_days,
        'hours': work_hours
    }
    
    # Save credentials
    save_credentials(user_credentials)
    
    # Update plot_times expected hours
    update_plot_times_expected_hours(user_id)
    
    # Show confirmation with schedule details
    schedule_details = []
    for day in enabled_days:
        hours = work_hours.get(day, 0.0)
        if hours > 0:
            schedule_details.append(f"{day_full_names[day]}: {hours}h")
    
    schedule_text = "\n".join(schedule_details)
    
    await query.edit_message_text(
        f"✅ Work schedule saved successfully!\n\n"
        f"Work days and hours:\n{schedule_text}\n\n"
        f"Total hours per week: {total_hours:.1f}h"
    )
    
    await show_menu_buttons(update, context)
    return ConversationHandler.END

def update_plot_times_expected_hours(user_id):
    """Update plot_times.EXPECTED_HOURS_BY_DAY based on user's work schedule."""
    if str(user_id) not in user_credentials or 'work_schedule' not in user_credentials[str(user_id)]:
        return
    
    work_schedule = user_credentials[str(user_id)]['work_schedule']
    work_days = work_schedule.get('days', {})
    work_hours = work_schedule.get('hours', {})
    
    # Update the global variable in plot_times
    for day in plot_times.EXPECTED_HOURS_BY_DAY:
        plot_times.EXPECTED_HOURS_BY_DAY[day] = work_hours.get(day, 0.0) if work_days.get(day, False) else 0.0
    
    logger.info(f"Updated expected hours for user {user_id}: {plot_times.EXPECTED_HOURS_BY_DAY}")

async def work_schedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Command handler for /work_schedule command."""
    user_id = update.effective_user.id
    logger.info(f"User {user_id} is setting work schedule")
    
    # Create a fake callback query to reuse the show_work_schedule_options function
    fake_query = FakeCallbackQuery(user_id)
    # Set the fake query as the callback_query attribute of the update
    update._callback_query = fake_query
    
    return await show_work_schedule_options(update, context)

async def start_part_time_custom(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start the process of setting a custom part-time work schedule."""
    query = update.callback_query
    user_id = query.from_user.id
    
    # Initialize with default or existing work days
    if str(user_id) in user_credentials and 'work_schedule' in user_credentials[str(user_id)]:
        work_days = user_credentials[str(user_id)]['work_schedule'].get('days', DEFAULT_WORK_DAYS.copy())
        work_hours = user_credentials[str(user_id)]['work_schedule'].get('hours', {})
    else:
        # For part-time custom, start with all weekdays enabled for selection
        work_days = {
            "Mon": True,
            "Tue": True,
            "Wed": True,
            "Thu": True,
            "Fri": True,
            "Sat": False,  # Always false for part-time
            "Sun": False   # Always false for part-time
        }
        work_hours = {}
    
    # Store in context for the conversation
    context.user_data['temp_work_days'] = work_days
    context.user_data['temp_work_hours'] = work_hours
    context.user_data['is_part_time_custom'] = True
    
    # Show work days selection
    return await show_part_time_days_selection(update, context)

async def show_part_time_days_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show the work days selection interface for part-time schedule."""
    query = update.callback_query
    
    # Get the temporary work days from context
    work_days = context.user_data.get('temp_work_days', DEFAULT_WORK_DAYS.copy())
    
    # Create keyboard with toggles for each day
    keyboard = []
    for day in ["Mon", "Tue", "Wed", "Thu", "Fri"]:  # Only weekdays for part-time
        status = "✅" if work_days.get(day, False) else "❌"
        keyboard.append([
            InlineKeyboardButton(f"{day}: {status}", callback_data=f"toggle_day_{day}")
        ])
    
    # Add save and continue buttons
    keyboard.append([
        InlineKeyboardButton("✅ Continue to Set Hours", callback_data="save_part_time_days"),
        InlineKeyboardButton("❌ Cancel", callback_data="cancel_work_schedule")
    ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Count selected days
    selected_days = sum(1 for day, enabled in work_days.items() if day in ["Mon", "Tue", "Wed", "Thu", "Fri"] and enabled)
    
    await query.edit_message_text(
        f"Select your part-time work days (Monday-Friday):\n"
        f"(Click on a day to toggle it on/off)\n\n"
        f"Selected days: {selected_days}/5\n"
        f"After selecting days, you'll be able to set specific hours for each day.",
        reply_markup=reply_markup
    )
    
    return SETTING_WORK_DAYS

async def save_part_time_days(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save the selected part-time work days and proceed to hours setup."""
    query = update.callback_query
    
    # Get the temporary work days from context
    work_days = context.user_data.get('temp_work_days', DEFAULT_WORK_DAYS.copy())
    
    # Calculate selected days count (only weekdays)
    selected_days_count = sum(1 for day, enabled in work_days.items() 
                             if day in ["Mon", "Tue", "Wed", "Thu", "Fri"] and enabled)
    
    if selected_days_count == 0:
        await query.edit_message_text(
            "You must select at least one work day. Please try again."
        )
        return await show_part_time_days_selection(update, context)
    
    # Store selected days in context
    context.user_data['selected_days'] = [day for day, enabled in work_days.items() if enabled]
    
    # Show options for hours distribution
    keyboard = [
        [InlineKeyboardButton("⌛ Standard Part-Time (20h/week)", callback_data="hours_part_time")],
        [InlineKeyboardButton("📝 Set Specific Hours per Day", callback_data="set_specific_hours")],
        [InlineKeyboardButton("◀️ Back to Day Selection", callback_data="back_to_days")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_work_schedule")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    days_text = ", ".join([day for day, enabled in work_days.items() if enabled])
    
    await query.edit_message_text(
        f"You selected these work days: {days_text}\n\n"
        f"How would you like to set your work hours?",
        reply_markup=reply_markup
    )
    
    return SETTING_WORK_HOURS

async def email_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle email input for auto fetch."""
    user_id = update.effective_user.id
    email = update.message.text.strip()
    
    # Store email in context
    context.user_data['email'] = email
    
    await update.message.reply_text("Please enter your Odoo password")
    return WAITING_FOR_PASSWORD

async def password_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle password input and auto fetch tokens."""
    user_id = update.effective_user.id
    password = update.message.text.strip()
    
    # Delete the password message for security
    await update.message.delete()
    
    # Get stored values
    odoo_url = context.user_data.get('odoo_url')
    email = context.user_data.get('email')
    
    # Send a processing message
    processing_message = await update.message.reply_text("Fetching tokens from Odoo... This might take a moment.")
    
    try:
        # Log environment variables for debugging
        chrome_bin = os.environ.get("GOOGLE_CHROME_BIN")
        chromedriver_path = os.environ.get("CHROMEDRIVER_PATH")
        logger.info(f"Chrome binary path: {chrome_bin}")
        logger.info(f"ChromeDriver path: {chromedriver_path}")
        
        # Setup chrome options for Selenium
        chrome_options = webdriver.ChromeOptions()
        
        # Check if GOOGLE_CHROME_BIN is set, otherwise use default path for Heroku Chrome for Testing
        if chrome_bin:
            chrome_options.binary_location = chrome_bin
        else:
            # Use the default location for Chrome on Heroku's Chrome for Testing buildpack
            chrome_options.binary_location = "/app/.chrome-for-testing/chrome-linux64/chrome"
            logger.info("Using default Chrome binary location for Heroku Chrome for Testing")
        
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--no-sandbox")
        
        # Create a Service object with the chromedriver path
        chrome_service = Service(executable_path=chromedriver_path or "/app/.chrome-for-testing/chromedriver-linux64/chromedriver")
        
        # Initialize the driver with the service
        driver = webdriver.Chrome(service=chrome_service, options=chrome_options)
        
        # Navigate to the Odoo login page
        driver.get(odoo_url)
        time.sleep(2)  # Wait for page to load
        
        # Find and fill login fields
        email_field = driver.find_element(By.ID, "login")
        password_field = driver.find_element(By.ID, "password")
        
        email_field.clear()
        email_field.send_keys(email)
        
        password_field.clear()
        password_field.send_keys(password)
        
        # Click login button
        login_button = driver.find_element(By.XPATH, 
                      "//button[@type='submit' and contains(@class, 'btn-primary')]")
        login_button.click()
        
        # Check for login error messages
        try:
            # Wait a short time for error message to appear if login failed
            error_wait = WebDriverWait(driver, 5)
            error_element = error_wait.until(EC.presence_of_element_located(
                (By.CSS_SELECTOR, ".alert-danger, .o_error_message, .o_notification.o_notification_error")))
            
            # If we found an error element, this means login failed
            error_text = error_element.text.strip()
            if "password" in error_text.lower():
                error_message = "Wrong password. Please try again."
            else:
                error_message = f"Login failed: {error_text}"
            
            driver.quit()
            
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=processing_message.message_id,
                text=error_message
            )
            await show_menu_buttons(update, context)
            return ConversationHandler.END
            
        except Exception:
            # No error message found, continue with normal flow
            pass
        
        # Wait for login to complete and dashboard to load
        try:
            wait = WebDriverWait(driver, 20)
            wait.until(EC.presence_of_element_located((By.CLASS_NAME, "o_app")))
        except Exception:
            # If we time out waiting for the dashboard, assume login failed
            driver.quit()
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=processing_message.message_id,
                text="Login failed. Please check your email and password and try again."
            )
            await show_menu_buttons(update, context)
            return ConversationHandler.END
        
        # Get cookies for session ID
        all_cookies = driver.get_cookies()
        session_id = None
        for cookie in all_cookies:
            if cookie['name'] == 'session_id':
                session_id = cookie['value']
                break
        
        # Get CSRF token from various sources
        csrf_token = None
        
        # Method 1: From localStorage
        try:
            csrf_token = driver.execute_script("return localStorage.getItem('csrf_token');")
        except:
            pass
        
        # Method 2: From page source
        if not csrf_token:
            try:
                page_source = driver.page_source
                csrf_match = re.search(r"csrf_token\s*:\s*['\"]([^'\"]+)['\"]", page_source)
                if csrf_match:
                    csrf_token = csrf_match.group(1)
            except:
                pass
        
        # Method 3: From sessionStorage
        if not csrf_token:
            try:
                csrf_token = driver.execute_script("return sessionStorage.getItem('csrf_token');")
            except:
                pass
                
        # Method 4: From odoo namespace in window
        if not csrf_token:
            try:
                csrf_token = driver.execute_script("return odoo.csrf_token;")
            except:
                pass
        
        # Close the driver
        driver.quit()
        
        # Check if we successfully got the tokens
        if not session_id or not csrf_token:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=processing_message.message_id,
                text="Failed to fetch authentication tokens. Please try manual credential entry or check your login details."
            )
            await show_menu_buttons(update, context)
            return ConversationHandler.END
        
        # Store session_id and csrf_token in context
        context.user_data['session_id'] = session_id
        context.user_data['csrf_token'] = csrf_token
        
        # Ask for Odoo UID
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=processing_message.message_id,
            text="Successfully fetched tokens! Please enter your Odoo User ID (UID)."
        )
        return WAITING_FOR_ODOO_UID
        
    except Exception as e:
        logger.error(f"Error in auto fetch: {str(e)}", exc_info=True)
        
        # Check for common authentication errors based on error message
        error_message = str(e)
        if "chrome not reachable" in error_message.lower():
            user_message = "Browser connection error. Please try again later."
        elif "no such element" in error_message.lower():
            user_message = "Login page elements not found. The Odoo website structure may have changed."
        elif "timeout" in error_message.lower():
            user_message = "Login timed out. Please check your credentials and try again."
        elif "invalid username or password" in error_message.lower() or "password" in error_message.lower():
            user_message = "Invalid username or password. Please check your credentials and try again."
        else:
            # For other errors, display a user-friendly message
            user_message = "Error fetching tokens. Please try manual credential entry."
        
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=processing_message.message_id,
            text=user_message
        )
        await show_menu_buttons(update, context)
        return ConversationHandler.END

if __name__ == '__main__':
    main() 