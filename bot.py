import os, asyncio
from threading import Thread
from dotenv import load_dotenv
from flask import Flask

import socket

import time
from datetime import datetime, timezone

LAST_STATE = None        # None/True/False
UP_SINCE: datetime | None = None


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
MC_TYPE = os.getenv("MC_TYPE", "auto").lower()  # "auto" | "java" | "bedrock"
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

def tcp_open(host, port, timeout=3):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False

async def ping_server():
    # 1) If the port isn't open, it's definitely DOWN.
    if not tcp_open(MC_HOST, MC_PORT, timeout=3):
        return False, 0, None

    try_java = MC_TYPE in ("auto", "java")
    try_bed  = MC_TYPE in ("auto", "bedrock") and BedrockServer is not None

    # Try Java first
    if try_java:
        try:
            start = time.perf_counter()
            srv = JavaServer.lookup(f"{MC_HOST}:{MC_PORT}")
            status = await asyncio.to_thread(srv.status)
            latency_ms = int((time.perf_counter() - start) * 1000)
            online = getattr(status.players, "online", 0) or 0
            # mcstatus for Java also has status.latency (ms), but we compute our own for both paths
            return True, online, latency_ms
        except Exception:
            if MC_TYPE == "java":
                return False, 0, None

    # Then Bedrock
    if try_bed:
        try:
            start = time.perf_counter()
            srv = BedrockServer.lookup(f"{MC_HOST}:{MC_PORT}")
            status = await asyncio.to_thread(srv.status)
            latency_ms = int((time.perf_counter() - start) * 1000)
            players = getattr(getattr(status, "players", None), "online", 0) or 0
            return True, players, latency_ms
        except Exception:
            pass

    # Port is open but neither status worked → treat as DOWN
    return False, 0, None


def fmt_uptime(since: datetime | None) -> str:
    if not since:
        return "—"
    delta = datetime.now(timezone.utc) - since
    total = int(delta.total_seconds())
    d, rem = divmod(total, 86400)
    h, rem = divmod(rem, 3600)
    m, _  = divmod(rem, 60)
    parts = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    parts.append(f"{m}m")
    return " ".join(parts)

@tasks.loop(seconds=INTERVAL)
async def updater():
    global LAST_STATE, UP_SINCE

    await client.wait_until_ready()
    ch = client.get_channel(CHANNEL)
    if ch is None:
        print(f"Channel {CHANNEL} not found")
        return

    is_up, online, latency_ms = await ping_server()

    # track uptime transitions
    if is_up and LAST_STATE is not True:
        UP_SINCE = datetime.now(timezone.utc)
    if not is_up:
        UP_SINCE = None
    LAST_STATE = is_up

    color = 0x57F287 if is_up else 0xED4245  # green / red
    status_text = "ONLINE" if is_up else "OFFLINE"

    desc_lines = [
        f"**Host:** `{MC_HOST}:{MC_PORT}`",
        f"**Status:** {'🟢' if is_up else '🔴'} **{status_text}**",
        f"**Players:** {online if is_up else 0}",
        f"**Latency:** {f'{latency_ms} ms' if latency_ms is not None else '—'}",
        f"**Uptime:** {fmt_uptime(UP_SINCE)}",
    ]
    embed = discord.Embed(
        title="Minecraft Server Status",
        description="\n".join(desc_lines),
        color=color
    )
    embed.set_footer(text="Auto-checked every {0}s".format(INTERVAL))

    # edit one pinned message if MESSAGE_ID set; else send new
    if MESSAGE_ID:
        try:
            msg = await ch.fetch_message(MESSAGE_ID)
            await msg.edit(embed=embed, content=None)
            return
        except Exception as e:
            print("Edit failed:", e)

    m = await ch.send(embed=embed)
    print(f"STATUS_MESSAGE_ID={m.id}")


@tree.command(name="mcstatus", description="Check Minecraft server status")
async def mcstatus_cmd(interaction: discord.Interaction):
    global LAST_STATE, UP_SINCE
    is_up, online, latency_ms = await ping_server()
    # update uptime tracker for manual checks as well
    if is_up and LAST_STATE is not True:
        UP_SINCE = datetime.now(timezone.utc)
    if not is_up:
        UP_SINCE = None
    LAST_STATE = is_up

    color = 0x57F287 if is_up else 0xED4245
    status_text = "ONLINE" if is_up else "OFFLINE"
    desc = [
        f"**Host:** `{MC_HOST}:{MC_PORT}`",
        f"**Status:** {'🟢' if is_up else '🔴'} **{status_text}**",
        f"**Players:** {online if is_up else 0}",
        f"**Latency:** {f'{latency_ms} ms' if latency_ms is not None else '—'}",
        f"**Uptime:** {fmt_uptime(UP_SINCE)}",
    ]
    embed = discord.Embed(
        title="Minecraft Server Status",
        description="\n".join(desc),
        color=color
    )
    await interaction.response.send_message(embed=embed)

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
