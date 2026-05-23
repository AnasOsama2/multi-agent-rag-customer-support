import os
from contextlib import asynccontextmanager
from typing import Dict
from fastapi import FastAPI, Request, BackgroundTasks
import httpx
from langchain_core.messages import ToolMessage, AIMessage
from customer_support_chat.app.graph import multi_agentic_graph
from customer_support_chat.app.services.utils import download_and_prepare_db
from customer_support_chat.app.core.logger import logger
from customer_support_chat.app.core.settings import get_settings

settings = get_settings()

# Read env variables or config
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_WEBHOOK_URL = os.environ.get("TELEGRAM_WEBHOOK_URL", "")

# In-memory mapping of chat_id -> passenger_id
passenger_map: Dict[int, str] = {}

async def send_telegram_message(chat_id: int, text: str):
    """Utility to send a message back to the Telegram chat asynchronously."""
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN not configured. Cannot send message.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
    except Exception as e:
        logger.error(f"Failed to send Telegram message to {chat_id}: {e}")

async def process_telegram_update(update: dict):
    """Processes the incoming update from Telegram in a background task."""
    try:
        if "message" not in update:
            return
        
        message = update["message"]
        chat_id = message["chat"]["id"]
        text = message.get("text", "").strip()

        if not text:
            return

        # Handlers for Telegram slash commands
        if text.startswith("/start"):
            welcome_text = (
                "✈️ <b>Welcome to the Swiss Airlines Multi-Agent Customer Support Bot!</b>\n\n"
                "I can help you search for flights, book hotel reservations, "
                "rent cars, find excursion recommendations, and query company policies.\n\n"
                "🛠️ <b>Commands:</b>\n"
                "• <code>/passenger &lt;id&gt;</code> - Register/change your Swiss Airlines passenger ID "
                "(defaults to demo passenger ID <code>5102 899977</code>)\n\n"
                "How can I help you today?"
            )
            await send_telegram_message(chat_id, welcome_text)
            return

        elif text.startswith("/passenger"):
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                await send_telegram_message(chat_id, "❌ Please specify a passenger ID. Format: <code>/passenger [id]</code>")
                return
            new_id = parts[1].strip()
            passenger_map[chat_id] = new_id
            await send_telegram_message(
                chat_id, 
                f"✅ Registered passenger ID <code>{new_id}</code> for this conversation."
            )
            return

        # Prepare thread configuration
        passenger_id = passenger_map.get(chat_id, "5102 899977")
        config = {
            "configurable": {
                "passenger_id": passenger_id,
                "thread_id": str(chat_id),
            }
        }

        # Retrieve the current state before invoke to compare message IDs
        current_state = multi_agentic_graph.get_state(config)
        prev_message_ids = {msg.id for msg in (current_state.value.get("messages", []) if current_state.value else [])}

        # Check if the graph is currently interrupted
        if current_state.next:
            # Graph is interrupted waiting for tool approval
            if text.lower() in ["y", "yes", "approve", "confirm"]:
                # User approved: continue graph execution by passing None
                result = multi_agentic_graph.invoke(None, config)
            else:
                # User denied or provided feedback: construct ToolMessage with the reasoning
                tool_call_id = current_state.value["messages"][-1].tool_calls[0]["id"]
                result = multi_agentic_graph.invoke(
                    {
                        "messages": [
                            ToolMessage(
                                tool_call_id=tool_call_id,
                                content=f"API call denied by user. Reasoning: '{text}'. Continue assisting, accounting for the user's input.",
                            )
                        ]
                    },
                    config,
                )
        else:
            # Normal user query flow: invoke the graph with the message
            result = multi_agentic_graph.invoke(
                {"messages": [("user", text)]},
                config,
            )

        # Retrieve the updated state to send the newly generated messages
        new_state = multi_agentic_graph.get_state(config)
        all_messages = new_state.value.get("messages", []) if new_state.value else []
        new_messages = [msg for msg in all_messages if msg.id not in prev_message_ids]

        # Send AIMessages back to the user
        for msg in new_messages:
            if isinstance(msg, AIMessage) or getattr(msg, "type", "") == "ai":
                if msg.content:
                    await send_telegram_message(chat_id, msg.content)

        # Check if the new state resulted in a new interrupt
        if new_state.next:
            # Prompt the user for sensitive operation approval
            approval_prompt = (
                "⚠️ <b>SENSITIVE OPERATION DETECTED</b>\n\n"
                "The assistant wants to make a sensitive modification to your booking. "
                "Do you approve this change?\n\n"
                "Reply with <b>'yes'</b> to approve and continue, or describe your desired changes to decline."
            )
            await send_telegram_message(chat_id, approval_prompt)

    except Exception as e:
        logger.error(f"Error processing Telegram update: {e}")
        try:
            await send_telegram_message(
                chat_id, 
                "❌ An unexpected error occurred while processing your request. Please try again."
            )
        except Exception:
            pass

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure database is prepared
    download_and_prepare_db()
    
    # Configure Telegram Webhook on startup
    if TELEGRAM_BOT_TOKEN and TELEGRAM_WEBHOOK_URL:
        webhook_endpoint = f"{TELEGRAM_WEBHOOK_URL}/telegram-webhook"
        logger.info(f"Registering Telegram webhook at endpoint: {webhook_endpoint}")
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json={"url": webhook_endpoint})
                resp.raise_for_status()
                logger.info(f"Telegram webhook set successfully: {resp.json()}")
        except Exception as e:
            logger.error(f"Failed to register Telegram webhook on startup: {e}")
    else:
        logger.warning(
            "TELEGRAM_BOT_TOKEN or TELEGRAM_WEBHOOK_URL not configured. "
            "FastAPI app running, but Telegram webhooks will not be registered automatically."
        )
    yield

app = FastAPI(
    title="Swiss Airlines Customer Support Chat API",
    version="1.0.0",
    lifespan=lifespan
)

@app.post("/telegram-webhook")
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    """Webhook endpoint to receive updates from Telegram."""
    try:
        update = await request.json()
        background_tasks.add_task(process_telegram_update, update)
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Error receiving Telegram webhook payload: {e}")
        return {"status": "error", "detail": str(e)}

@app.get("/health")
def health():
    return {"status": "healthy"}
