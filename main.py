import os
import random
import sqlite3
import time
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

TOKEN = os.getenv("DISCORD_TOKEN")
DB_PATH = os.getenv("NEXUS_DB_PATH", "nexus.sqlite3")

if not TOKEN:
    raise RuntimeError("Missing DISCORD_TOKEN environment variable. Add your bot token in the hosting panel.")

intents = discord.Intents.default()
intents.message_content = True  # Required for XP from messages; enable it in the Discord Developer Portal too.
intents.members = True          # Required for member-related events and richer user information.

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
started_at = time.time()

# --- Database ---
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            xp INTEGER NOT NULL DEFAULT 0,
            level INTEGER NOT NULL DEFAULT 0,
            coins INTEGER NOT NULL DEFAULT 0,
            last_daily TEXT,
            last_work REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (guild_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS inventory (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            item TEXT NOT NULL,
            amount INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (guild_id, user_id, item)
        );
        CREATE TABLE IF NOT EXISTS cooldowns (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            last_used REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (guild_id, user_id, action)
        );
        """)

def ensure_user(guild_id: int, user_id: int):
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (guild_id, user_id) VALUES (?, ?)",
            (guild_id, user_id),
        )

def get_user(guild_id: int, user_id: int):
    ensure_user(guild_id, user_id)
    with db() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        ).fetchone()

def add_coins(guild_id: int, user_id: int, amount: int):
    ensure_user(guild_id, user_id)
    with db() as conn:
        conn.execute(
            "UPDATE users SET coins = MAX(0, coins + ?) WHERE guild_id=? AND user_id=?",
            (amount, guild_id, user_id),
        )

def add_item(guild_id: int, user_id: int, item: str, amount: int = 1):
    with db() as conn:
        conn.execute("""
            INSERT INTO inventory (guild_id, user_id, item, amount) VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id, item) DO UPDATE SET amount = MAX(0, amount + excluded.amount)
        """, (guild_id, user_id, item, amount))
        conn.execute("DELETE FROM inventory WHERE guild_id=? AND user_id=? AND amount<=0",
                     (guild_id, user_id))

def get_inventory(guild_id: int, user_id: int):
    with db() as conn:
        return conn.execute(
            "SELECT item, amount FROM inventory WHERE guild_id=? AND user_id=? AND amount>0 ORDER BY item",
            (guild_id, user_id),
        ).fetchall()

def embed(title: str, description: str = ""):
    e = discord.Embed(title=title, description=description, color=discord.Color.from_rgb(137, 169, 142))
    e.set_footer(text="NEXUS • herramientas, progreso y supervivencia")
    return e

def guild_only(interaction: discord.Interaction) -> bool:
    return interaction.guild is not None

def require_guild():
    async def predicate(interaction: discord.Interaction):
        if interaction.guild is None:
            raise app_commands.CheckFailure("Este comando solo funciona dentro de un servidor.")
        return True
    return app_commands.check(predicate)

# --- Startup ---
@bot.event
async def on_ready():
    init_db()
    try:
        synced = await bot.tree.sync()
        print(f"Sincronizados {len(synced)} comandos de barra.")
    except Exception as exc:
        print(f"Error sincronizando comandos: {exc}")
    print(f"NEXUS conectado como {bot.user} (ID: {bot.user.id})")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or message.guild is None:
        return
    # XP cooldown: at most one reward every 60 seconds per user/server.
    now = time.time()
    with db() as conn:
        row = conn.execute(
            "SELECT last_used FROM cooldowns WHERE guild_id=? AND user_id=? AND action='xp'",
            (message.guild.id, message.author.id),
        ).fetchone()
        if row is None or now - row["last_used"] >= 60:
            conn.execute("""
                INSERT INTO cooldowns (guild_id, user_id, action, last_used) VALUES (?, ?, 'xp', ?)
                ON CONFLICT(guild_id, user_id, action) DO UPDATE SET last_used=excluded.last_used
            """, (message.guild.id, message.author.id, now))
            conn.execute("INSERT OR IGNORE INTO users (guild_id, user_id) VALUES (?, ?)",
                         (message.guild.id, message.author.id))
            user = conn.execute("SELECT xp, level FROM users WHERE guild_id=? AND user_id=?",
                                (message.guild.id, message.author.id)).fetchone()
            new_xp = user["xp"] + random.randint(8, 15)
            new_level = int((new_xp / 100) ** 0.5)
            conn.execute("UPDATE users SET xp=?, level=? WHERE guild_id=? AND user_id=?",
                         (new_xp, new_level, message.guild.id, message.author.id))
    await bot.process_commands(message)

# --- General / developer tools ---
@bot.tree.command(name="help", description="Muestra las funciones principales de NEXUS")
async def help_command(interaction: discord.Interaction):
    e = embed("NEXUS • Centro de comandos")
    e.add_field(name="Herramientas", value="`/ping` `/botinfo` `/serverinfo` `/userinfo` `/avatar`", inline=False)
    e.add_field(name="Moderación", value="`/kick` `/ban` `/timeout` (requieren permisos)", inline=False)
    e.add_field(name="Niveles y economía", value="`/profile` `/balance` `/daily` `/work` `/leaderboard`", inline=False)
    e.add_field(name="Supervivencia", value="`/explore` `/inventory` `/gather` `/craft`", inline=False)
    e.add_field(name="Consejo", value="Escribe `/` en el chat para ver los comandos disponibles.", inline=False)
    await interaction.response.send_message(embed=e, ephemeral=True)

@bot.tree.command(name="ping", description="Comprueba la latencia de NEXUS")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(embed=embed("Conexión activa", f"Latencia: **{round(bot.latency * 1000)} ms**"))

@bot.tree.command(name="botinfo", description="Muestra información del bot")
async def botinfo(interaction: discord.Interaction):
    uptime = int(time.time() - started_at)
    hours, rem = divmod(uptime, 3600)
    minutes, seconds = divmod(rem, 60)
    e = embed("NEXUS • Estado", "Bot multiusos en Python con base de datos SQLite.")
    e.add_field(name="Versión", value="0.1.0", inline=True)
    e.add_field(name="Latencia", value=f"{round(bot.latency * 1000)} ms", inline=True)
    e.add_field(name="Tiempo activo", value=f"{hours} h {minutes} min {seconds} s", inline=True)
    await interaction.response.send_message(embed=e, ephemeral=True)

@bot.tree.command(name="serverinfo", description="Muestra información de este servidor")
@require_guild()
async def serverinfo(interaction: discord.Interaction):
    g = interaction.guild
    e = embed(f"Servidor • {g.name}")
    e.add_field(name="Miembros", value=str(g.member_count or "No disponible"), inline=True)
    e.add_field(name="Canales", value=str(len(g.channels)), inline=True)
    e.add_field(name="Creado", value=discord.utils.format_dt(g.created_at, style="D"), inline=True)
    if g.icon:
        e.set_thumbnail(url=g.icon.url)
    await interaction.response.send_message(embed=e, ephemeral=True)

@bot.tree.command(name="userinfo", description="Consulta información de un miembro")
@app_commands.describe(miembro="Miembro que quieres consultar")
@require_guild()
async def userinfo(interaction: discord.Interaction, miembro: discord.Member):
    e = embed(f"Perfil • {miembro.display_name}")
    e.add_field(name="Usuario", value=str(miembro), inline=True)
    e.add_field(name="ID", value=str(miembro.id), inline=True)
    e.add_field(name="Cuenta creada", value=discord.utils.format_dt(miembro.created_at, style="D"), inline=False)
    e.add_field(name="Se unió al servidor", value=discord.utils.format_dt(miembro.joined_at, style="D") if miembro.joined_at else "Desconocido", inline=False)
    e.set_thumbnail(url=miembro.display_avatar.url)
    await interaction.response.send_message(embed=e, ephemeral=True)

@bot.tree.command(name="avatar", description="Muestra el avatar de un miembro")
@app_commands.describe(miembro="Miembro cuyo avatar quieres ver")
async def avatar(interaction: discord.Interaction, miembro: discord.User = None):
    user = miembro or interaction.user
    e = embed(f"Avatar • {user.display_name}")
    e.set_image(url=user.display_avatar.url)
    await interaction.response.send_message(embed=e)

# --- Moderation ---
@bot.tree.command(name="kick", description="Expulsa a un miembro del servidor")
@app_commands.default_permissions(kick_members=True)
@app_commands.describe(miembro="Miembro a expulsar", motivo="Motivo de la expulsión")
@require_guild()
async def kick(interaction: discord.Interaction, miembro: discord.Member, motivo: str = "Sin motivo indicado"):
    if miembro.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
        await interaction.response.send_message("No puedes expulsar a alguien con un rol igual o superior al tuyo.", ephemeral=True)
        return
    try:
        await miembro.kick(reason=f"{motivo} | Por: {interaction.user}")
        await interaction.response.send_message(embed=embed("Miembro expulsado", f"{miembro.mention}\nMotivo: {motivo}"))
    except discord.Forbidden:
        await interaction.response.send_message("No tengo permisos para expulsar a ese miembro. Revisa mi rol y permisos.", ephemeral=True)

@bot.tree.command(name="ban", description="Banea a un miembro del servidor")
@app_commands.default_permissions(ban_members=True)
@app_commands.describe(miembro="Miembro a banear", motivo="Motivo del baneo")
@require_guild()
async def ban(interaction: discord.Interaction, miembro: discord.Member, motivo: str = "Sin motivo indicado"):
    if miembro.id == interaction.user.id or (miembro.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id):
        await interaction.response.send_message("No puedes banear a ese miembro.", ephemeral=True)
        return
    try:
        await miembro.ban(reason=f"{motivo} | Por: {interaction.user}")
        await interaction.response.send_message(embed=embed("Miembro baneado", f"{miembro} ha sido baneado.\nMotivo: {motivo}"))
    except discord.Forbidden:
        await interaction.response.send_message("No tengo permisos para banear a ese miembro. Revisa mi rol y permisos.", ephemeral=True)

@bot.tree.command(name="timeout", description="Limita temporalmente a un miembro")
@app_commands.default_permissions(moderate_members=True)
@app_commands.describe(miembro="Miembro", minutos="Duración entre 1 y 10080 minutos", motivo="Motivo")
@require_guild()
async def timeout(interaction: discord.Interaction, miembro: discord.Member, minutos: app_commands.Range[int, 1, 10080], motivo: str = "Sin motivo indicado"):
    if miembro.id == interaction.user.id or (miembro.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id):
        await interaction.response.send_message("No puedes aplicar timeout a ese miembro.", ephemeral=True)
        return
    try:
        await miembro.timeout(discord.utils.utcnow() + __import__("datetime").timedelta(minutes=minutos), reason=f"{motivo} | Por: {interaction.user}")
        await interaction.response.send_message(embed=embed("Timeout aplicado", f"{miembro.mention} • {minutos} minutos\nMotivo: {motivo}"))
    except discord.Forbidden:
        await interaction.response.send_message("No tengo permisos para aplicar timeout. Revisa mi rol y permisos.", ephemeral=True)

# --- Levels and economy ---
@bot.tree.command(name="profile", description="Muestra tu nivel, XP y monedas")
@app_commands.describe(miembro="Miembro cuyo perfil quieres ver")
@require_guild()
async def profile(interaction: discord.Interaction, miembro: discord.Member = None):
    target = miembro or interaction.user
    u = get_user(interaction.guild.id, target.id)
    e = embed(f"Perfil • {target.display_name}")
    e.set_thumbnail(url=target.display_avatar.url)
    e.add_field(name="Nivel", value=str(u["level"]), inline=True)
    e.add_field(name="XP", value=str(u["xp"]), inline=True)
    e.add_field(name="Monedas", value=f"{u['coins']:,} ◈", inline=True)
    e.add_field(name="Siguiente nivel", value=f"{max(0, ((u['level'] + 1) ** 2) * 100 - u['xp'])} XP aprox.", inline=False)
    await interaction.response.send_message(embed=e)

@bot.tree.command(name="balance", description="Consulta tu saldo de monedas")
@require_guild()
async def balance(interaction: discord.Interaction):
    u = get_user(interaction.guild.id, interaction.user.id)
    await interaction.response.send_message(embed=embed("Cartera", f"Tienes **{u['coins']:,} ◈**."), ephemeral=True)

@bot.tree.command(name="daily", description="Reclama tu recompensa diaria")
@require_guild()
async def daily(interaction: discord.Interaction):
    u = get_user(interaction.guild.id, interaction.user.id)
    today = datetime.now(timezone.utc).date().isoformat()
    if u["last_daily"] == today:
        await interaction.response.send_message("Ya has reclamado tu recompensa de hoy. Vuelve mañana.", ephemeral=True)
        return
    reward = random.randint(100, 200)
    with db() as conn:
        conn.execute("UPDATE users SET coins=coins+?, last_daily=? WHERE guild_id=? AND user_id=?",
                     (reward, today, interaction.guild.id, interaction.user.id))
    await interaction.response.send_message(embed=embed("Recompensa diaria", f"Has recibido **{reward} ◈**."))

@bot.tree.command(name="work", description="Trabaja para ganar monedas (enfriamiento de 30 minutos)")
@require_guild()
async def work(interaction: discord.Interaction):
    u = get_user(interaction.guild.id, interaction.user.id)
    now = time.time()
    if now - u["last_work"] < 1800:
        mins = int((1800 - (now - u["last_work"])) / 60) + 1
        await interaction.response.send_message(f"Necesitas descansar. Vuelve en unos **{mins} min**.", ephemeral=True)
        return
    reward = random.randint(25, 90)
    with db() as conn:
        conn.execute("UPDATE users SET coins=coins+?, last_work=? WHERE guild_id=? AND user_id=?",
                     (reward, now, interaction.guild.id, interaction.user.id))
    jobs = ["reparaste una radio", "recolectaste suministros", "ayudaste en el refugio", "reparaste una puerta"]
    await interaction.response.send_message(embed=embed("Turno completado", f"Has ganado **{reward} ◈** porque {random.choice(jobs)}."))

@bot.tree.command(name="leaderboard", description="Muestra los miembros con más XP")
@require_guild()
async def leaderboard(interaction: discord.Interaction):
    with db() as conn:
        rows = conn.execute("SELECT user_id, xp, level FROM users WHERE guild_id=? ORDER BY xp DESC LIMIT 10",
                            (interaction.guild.id,)).fetchall()
    lines = []
    for i, row in enumerate(rows, 1):
        lines.append(f"**{i}.** <@{row['user_id']}> — nivel {row['level']} · {row['xp']} XP")
    await interaction.response.send_message(embed=embed("Clasificación de XP", "\n".join(lines) if lines else "Todavía no hay datos."))

# --- Survival game ---
@bot.tree.command(name="explore", description="Explora las ruinas en busca de suministros")
@require_guild()
async def explore(interaction: discord.Interaction):
    user = interaction.user
    gid, uid = interaction.guild.id, user.id
    u = get_user(gid, uid)
    now = time.time()
    with db() as conn:
        row = conn.execute("SELECT last_used FROM cooldowns WHERE guild_id=? AND user_id=? AND action='explore'",
                           (gid, uid)).fetchone()
        if row and now - row["last_used"] < 300:
            mins = int((300 - (now - row["last_used"])) / 60) + 1
            await interaction.response.send_message(f"La zona no es segura todavía. Vuelve en unos **{mins} min**.", ephemeral=True)
            return
        conn.execute("""
            INSERT INTO cooldowns (guild_id, user_id, action, last_used) VALUES (?, ?, 'explore', ?)
            ON CONFLICT(guild_id, user_id, action) DO UPDATE SET last_used=excluded.last_used
        """, (gid, uid, now))
    loot_table = [("Madera", 2, 5), ("Chatarra", 1, 4), ("Tela", 1, 3), ("Comida", 1, 3), ("Nada útil", 0, 0)]
    item, low, high = random.choice(loot_table)
    if item == "Nada útil":
        await interaction.response.send_message(embed=embed("Exploración", "Recorriste un edificio abandonado, pero no encontraste nada útil. Prueba otra vez dentro de 5 minutos."))
        return
    amount = random.randint(low, high)
    add_item(gid, uid, item, amount)
    if random.random() < 0.35:
        coins = random.randint(10, 35)
        add_coins(gid, uid, coins)
        extra = f"\nTambién encontraste **{coins} ◈**."
    else:
        extra = ""
    await interaction.response.send_message(embed=embed("Exploración completada", f"Encontraste **{amount} × {item}**.{extra}"))

@bot.tree.command(name="inventory", description="Consulta tu inventario de supervivencia")
@require_guild()
async def inventory(interaction: discord.Interaction):
    rows = get_inventory(interaction.guild.id, interaction.user.id)
    desc = "\n".join(f"**{r['item']}** × {r['amount']}" for r in rows) if rows else "Tu inventario está vacío. Usa `/explore` para buscar recursos."
    await interaction.response.send_message(embed=embed("Inventario", desc), ephemeral=True)

@bot.tree.command(name="gather", description="Recoge recursos básicos en el refugio")
@require_guild()
async def gather(interaction: discord.Interaction):
    gid, uid = interaction.guild.id, interaction.user.id
    now = time.time()
    with db() as conn:
        row = conn.execute("SELECT last_used FROM cooldowns WHERE guild_id=? AND user_id=? AND action='gather'",
                           (gid, uid)).fetchone()
        if row and now - row["last_used"] < 120:
            await interaction.response.send_message("Necesitas esperar un poco antes de recoger más recursos.", ephemeral=True)
            return
        conn.execute("""
            INSERT INTO cooldowns (guild_id, user_id, action, last_used) VALUES (?, ?, 'gather', ?)
            ON CONFLICT(guild_id, user_id, action) DO UPDATE SET last_used=excluded.last_used
        """, (gid, uid, now))
    item = random.choice(["Madera", "Chatarra", "Tela"])
    amount = random.randint(1, 3)
    add_item(gid, uid, item, amount)
    await interaction.response.send_message(embed=embed("Recursos recogidos", f"Has conseguido **{amount} × {item}**."))

@bot.tree.command(name="craft", description="Fabrica un botiquín con tela y chatarra")
@require_guild()
async def craft(interaction: discord.Interaction):
    gid, uid = interaction.guild.id, interaction.user.id
    rows = {r["item"]: r["amount"] for r in get_inventory(gid, uid)}
    if rows.get("Tela", 0) < 2 or rows.get("Chatarra", 0) < 1:
        await interaction.response.send_message("Necesitas **2 × Tela** y **1 × Chatarra** para fabricar un botiquín.", ephemeral=True)
        return
    add_item(gid, uid, "Tela", -2)
    add_item(gid, uid, "Chatarra", -1)
    add_item(gid, uid, "Botiquín", 1)
    await interaction.response.send_message(embed=embed("Fabricación completada", "Has fabricado **1 × Botiquín** usando 2 de Tela y 1 de Chatarra."))

# --- Errors and run ---
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        message = str(error) or "No puedes usar este comando aquí."
    elif isinstance(error, app_commands.MissingPermissions):
        message = "No tienes permisos para usar este comando."
    else:
        print(f"Error en comando: {repr(error)}")
        message = "Ha ocurrido un error. El equipo de NEXUS puede revisar la consola para ver los detalles."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        pass

init_db()
bot.run(TOKEN)
