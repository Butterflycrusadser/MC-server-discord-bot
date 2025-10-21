import os, asyncio
from threading import Thread
from dotenv import load_dotenv
from flask import Flask

import discord
from discord.ext import tasks
from mcstatus import JavaServer
try:
    from mcstatus import BedrockServer  # optional for bedrock
except Exception:
    BedrockServer = None

load_dotenv()

TOKEN   = os.getenv("DISCORD_TOKEN")
CHANNEL = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
MC_HOST = os.getenv("MC_HOST", "127.0.0.1")
MC_PORT = int(os.getenv("MC_PORT", "25565"))
MC_TYPE = os.getenv("MC_TYPE", "java").lower()  # "java" or "bedrock"
MESSAGE_ID = int(os.getenv("STATUS_MESSAGE_ID", "0"))  # optional fixed message id
INTERVAL = int(os.getenv("CHECK_INTERVAL_SEC", "60"))

# tiny Flask app so the host sees a web port
app = Flask(__name__)

@app.get("/")
def ok():
    return "OK"

def run_flask():
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))

# Discord
intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = discord.app_commands.CommandTree(client)

async def ping_server():
    try:
        if MC_TYPE == "bedrock" and BedrockServer:
            srv = BedrockServer.lookup(f"{MC_HOST}:{MC_PORT}")
            status = await asyncio.to_thread(srv.status)
            # Bedrock doesn't return players.online reliably everywhere
            online = getattr(getattr(status, "players", None), "online", 0) or 0
            return True, online
        else:
            srv = JavaServer.lookup(f"{MC_HOST}:{MC_PORT}")
            status = await asyncio.to_thread(srv.status)
            return True, status.players.online
    except Exception:
        return False, 0

def status_line(is_up, online):
    if is_up:
        return f"🟢 {MC_HOST}:{MC_PORT} is **UP** | Players online: **{online}**"
    return f"🔴 {MC_HOST}:{MC_PORT} is **DOWN**"

@tasks.loop(seconds=INTERVAL)
async def updater():
    await client.wait_until_ready()
    ch = client.get_channel(CHANNEL)
    if ch is None:
        return
    is_up, online = await ping_server()
    text = status_line(is_up, online)
    if MESSAGE_ID:
        try:
            msg = await ch.fetch_message(MESSAGE_ID)
            await msg.edit(content=text)
            return
        except Exception:
            pass
    m = await ch.send(text)
    # allow capturing the new message id from logs if needed
    print(f"STATUS_MESSAGE_ID={m.id}")

@tree.command(name="mcstatus", description="Check Minecraft server status")
async def mcstatus_cmd(interaction: discord.Interaction):
    is_up, online = await ping_server()
    await interaction.response.send_message(status_line(is_up, online))

@client.event
async def on_ready():
    try:
        await tree.sync()
    except Exception:
        pass
    if not updater.is_running():
        updater.start()
    print(f"Logged in as {client.user}")

if __name__ == "__main__":
    Thread(target=run_flask, daemon=True).start()
    client.run(TOKEN)
