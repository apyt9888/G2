import discord
from discord.ext import commands
import aiosqlite
import time
import os
from collections import defaultdict

# ================== ENV ==================
TOKEN = os.getenv("TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))
CLIENT_ID = int(os.getenv("CLIENT_ID"))

# ================== BOT ==================
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

DB = "database.db"

spam = defaultdict(list)
cooldown = {}

db = None

# ================== DB ==================
async def execute(query, args=(), fetchone=False, fetchall=False):
    global db
    cur = await db.execute(query, args)

    if fetchone:
        return await cur.fetchone()
    if fetchall:
        return await cur.fetchall()

    await db.commit()

async def setup_db():
    global db
    db = await aiosqlite.connect(DB)

    await db.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER,
        guild INTEGER,
        xp INTEGER DEFAULT 0,
        lvl INTEGER DEFAULT 0,
        PRIMARY KEY(id, guild)
    );

    CREATE TABLE IF NOT EXISTS stats (
        id INTEGER,
        guild INTEGER,
        messages INTEGER DEFAULT 0,
        voice INTEGER DEFAULT 0,
        images INTEGER DEFAULT 0,
        videos INTEGER DEFAULT 0,
        PRIMARY KEY(id, guild)
    );

    CREATE TABLE IF NOT EXISTS voice (
        id INTEGER,
        guild INTEGER,
        join_time INTEGER
    );

    CREATE TABLE IF NOT EXISTS rewards (
        guild INTEGER,
        lvl INTEGER,
        role INTEGER,
        msg TEXT
    );

    CREATE TABLE IF NOT EXISTS lvlmsg (
        guild INTEGER,
        lvl INTEGER,
        msg TEXT
    );

    CREATE TABLE IF NOT EXISTS alias (
        guild INTEGER,
        name TEXT,
        command TEXT
    );
    """)

    await db.commit()

# ================== LEVEL ==================
def need(lvl):
    return 100 + (lvl * 50)

# ================== MESSAGE ==================
@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return

    uid = message.author.id
    gid = message.guild.id
    now = time.time()

    # spam
    spam[uid].append(now)
    spam[uid] = [t for t in spam[uid] if now - t < 5]

    if len(spam[uid]) > 5:
        return await message.channel.send(f"🚫 {message.author.mention} لا تسوي سبام")

    if uid in cooldown and now - cooldown[uid] < 2:
        return
    cooldown[uid] = now

    # alias
    row = await execute(
        "SELECT command FROM alias WHERE guild=? AND name=?",
        (gid, message.content),
        fetchone=True
    )

    if row:
        ctx = await bot.get_context(message)
        ctx.message.content = row[0]
        return await bot.invoke(ctx)

    # stats init
    await execute("INSERT OR IGNORE INTO stats VALUES (?,?,0,0,0,0)", (uid, gid))

    m, img, vid = await execute(
        "SELECT messages,images,videos FROM stats WHERE id=? AND guild=?",
        (uid, gid),
        fetchone=True
    ) or (0, 0, 0)

    m += 1

    if message.attachments:
        for a in message.attachments:
            if a.content_type:
                if "image" in a.content_type:
                    img += 1
                elif "video" in a.content_type:
                    vid += 1

    await execute(
        "UPDATE stats SET messages=?,images=?,videos=? WHERE id=? AND guild=?",
        (m, img, vid, uid, gid)
    )

    # XP
    await execute("INSERT OR IGNORE INTO users VALUES (?,?,0,0)", (uid, gid))

    xp, lvl = await execute(
        "SELECT xp,lvl FROM users WHERE id=? AND guild=?",
        (uid, gid),
        fetchone=True
    ) or (0, 0)

    xp += 15

    if xp >= need(lvl):
        xp = 0
        lvl += 1

        msg = await execute(
            "SELECT msg FROM lvlmsg WHERE guild=? AND lvl=?",
            (gid, lvl),
            fetchone=True
        )

        text = msg[0].format(user=message.author.mention, level=lvl) if msg else f"{message.author.mention} وصل لفل {lvl}"
        await message.channel.send(text)

    await execute(
        "UPDATE users SET xp=?,lvl=? WHERE id=? AND guild=?",
        (xp, lvl, uid, gid)
    )

    await bot.process_commands(message)

# ================== VOICE ==================
@bot.event
async def on_voice_state_update(member, before, after):
    if not member.guild:
        return

    gid = member.guild.id

    if after.channel and not before.channel:
        await execute(
            "INSERT INTO voice VALUES (?,?,?)",
            (member.id, gid, int(time.time()))
        )

    if before.channel and not after.channel:
        row = await execute(
            "SELECT join_time FROM voice WHERE id=? AND guild=?",
            (member.id, gid),
            fetchone=True
        )

        if row:
            duration = int(time.time()) - row[0]

            await execute(
                "UPDATE stats SET voice = voice + ? WHERE id=? AND guild=?",
                (duration, member.id, gid)
            )

            await execute(
                "DELETE FROM voice WHERE id=? AND guild=?",
                (member.id, gid)
            )

# ================== PROFILE ==================
@bot.tree.command(name="بروفايل")
async def profile(interaction: discord.Interaction, عضو: discord.Member = None):
    عضو = عضو or interaction.user

    u = await execute(
        "SELECT xp,lvl FROM users WHERE id=? AND guild=?",
        (عضو.id, interaction.guild.id),
        fetchone=True
    )

    s = await execute(
        "SELECT messages,voice,images,videos FROM stats WHERE id=? AND guild=?",
        (عضو.id, interaction.guild.id),
        fetchone=True
    )

    emb = discord.Embed(title=عضو.name, color=discord.Color.blue())

    if u:
        emb.add_field(name="Level", value=u[1])
        emb.add_field(name="XP", value=u[0])

    if s:
        emb.add_field(name="Messages", value=s[0])
        emb.add_field(name="Voice", value=f"{s[1]} sec")
        emb.add_field(name="Images", value=s[2])
        emb.add_field(name="Videos", value=s[3])

    await interaction.response.send_message(embed=emb)

# ================== LEADERBOARD ==================
@bot.tree.command(name="ترتيب")
async def lb(interaction: discord.Interaction, نوع: str):

    col = {
        "رسائل": "messages",
        "فويس": "voice",
        "صور": "images",
        "فيديو": "videos"
    }

    if نوع not in col:
        return await interaction.response.send_message("رسائل / فويس / صور / فيديو")

    rows = await execute(
        f"SELECT id,{col[نوع]} FROM stats WHERE guild=? ORDER BY {col[نوع]} DESC LIMIT 10",
        (interaction.guild.id,),
        fetchall=True
    )

    text = f"🏆 {نوع}:\n\n"
    for i, r in enumerate(rows, 1):
        text += f"{i}- <@{r[0]}> | {r[1]}\n"

    await interaction.response.send_message(text)

# ================== READY ==================
@bot.event
async def on_ready():
    await setup_db()

    guild = discord.Object(id=GUILD_ID)

    bot.tree.copy_global_to(guild=guild)
    await bot.tree.sync(guild=guild)

    print("🔥 BOT ONLINE + SLASH FAST")

# ================== RUN ==================
bot.run(TOKEN)
