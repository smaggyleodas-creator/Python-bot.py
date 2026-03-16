import discord

from discord.ext import commands

import aiohttp, asyncio, os, random, datetime, string, aiosqlite

from dotenv import load_dotenv



# --- 1. CONFIGURATION ---

load_dotenv()

TOKEN = os.getenv('BOT_TOKEN')

DB_FILE = 'database.db'

OWNER_ID = 1457428548582248615 



# --- 2. DATABASE INITIALIZATION ---

async def init_db():

    async with aiosqlite.connect(DB_FILE) as db:

        await db.execute('''CREATE TABLE IF NOT EXISTS users 

            (user_id TEXT PRIMARY KEY, plan TEXT DEFAULT 'NONE', expiry TEXT DEFAULT '', 

             max_slots INTEGER DEFAULT 0, max_camps INTEGER DEFAULT 0, total_sent INTEGER DEFAULT 0,

             accepted_tos INTEGER DEFAULT 0)''')

        await db.execute('''CREATE TABLE IF NOT EXISTS slots 

            (user_id TEXT, slot_name TEXT, token TEXT DEFAULT '', PRIMARY KEY(user_id, slot_name))''')

        await db.execute('''CREATE TABLE IF NOT EXISTS campaigns 

            (user_id TEXT, slot_name TEXT, camp_name TEXT, channels TEXT, msg TEXT, delay INTEGER DEFAULT 60, active INTEGER DEFAULT 0,

             PRIMARY KEY(user_id, slot_name, camp_name))''')

        await db.execute('''CREATE TABLE IF NOT EXISTS keys (key_code TEXT PRIMARY KEY, data TEXT, created_at TEXT)''')

        await db.commit()



# --- 3. HELPER LOGIC ---

async def get_user_data(u_id):

    async with aiosqlite.connect(DB_FILE) as db:

        db.row_factory = aiosqlite.Row

        async with db.execute("SELECT * FROM users WHERE user_id = ?", (str(u_id),)) as cur:

            user = await cur.fetchone()

            if not user:

                await db.execute("INSERT INTO users (user_id) VALUES (?)", (str(u_id),))

                await db.commit()

                return await get_user_data(u_id)

            return user



async def get_dashboard_embed(u_id):

    user = await get_user_data(u_id)

    

    async with aiosqlite.connect(DB_FILE) as db:

        async with db.execute("SELECT COUNT(*) FROM slots WHERE user_id = ?", (str(u_id),)) as cur:

            curr_slots = (await cur.fetchone())[0]

        async with db.execute("SELECT COUNT(*) FROM campaigns WHERE user_id = ?", (str(u_id),)) as cur:

            curr_camps = (await cur.fetchone())[0]



    plan = user['plan']

    is_active = plan != "NONE"

    color = 0xFEE75C if plan.upper() == "RADIANT" else 0x2b2d31

    

    embed = discord.Embed(title="🏆 EliteFlow Extreme | Dashboard", color=color)

    

    if not is_active:

        embed.description = "❌ **Access Denied**\nYou must redeem a key to use the dashboard."

        return embed



    embed.add_field(name="✨ Plan", value=f"`{plan}`", inline=True)

    embed.add_field(name="📩 Sent", value=f"`{user['total_sent']:,}`", inline=True)

    embed.add_field(name="📁 Slots", value=f"`{curr_slots}/{user['max_slots']}`", inline=True)

    embed.add_field(name="🚀 Campaigns", value=f"`{curr_camps}/{user['max_camps']}`", inline=True)

    

    if user['expiry']:

        embed.add_field(name="⌛ Expiry", value=f"`{user['expiry'].split('T')[0]}`", inline=False)

        

    embed.set_footer(text="Secure Session Locked")

    return embed



# --- 4. ENGINE: CAMPAIGN LOOP (UNCHANGED) ---

async def campaign_loop(u_id, s_name, c_name):

    while True:

        async with aiosqlite.connect(DB_FILE) as db:

            db.row_factory = aiosqlite.Row

            async with db.execute("SELECT c.*, s.token FROM campaigns c JOIN slots s ON c.user_id = s.user_id AND c.slot_name = s.slot_name WHERE c.user_id = ? AND c.slot_name = ? AND c.camp_name = ?", (u_id, s_name, c_name)) as cur:

                row = await cur.fetchone()

        

        if not row or not row['active'] or not row['token']: break

        

        try:

            headers = {"Authorization": row['token'], "Content-Type": "application/json"}

            for ch_id in row['channels'].split(","):

                async with aiohttp.ClientSession() as session:

                    payload = {"content": row['msg'], "nonce": str(random.randint(10**18, 10**19))}

                    async with session.post(f"https://discord.com/api/v9/channels/{ch_id.strip()}/messages", headers=headers, json=payload) as r:

                        if r.status in [200, 201]:

                            async with aiosqlite.connect(DB_FILE) as db:

                                await db.execute("UPDATE users SET total_sent = total_sent + 1 WHERE user_id = ?", (str(u_id),))

                                await db.commit()

            await asyncio.sleep(max(60, row['delay']))

        except: await asyncio.sleep(30)



# --- 5. INTERFACE VIEWS ---



# [NEW: LOCKED VIEW]

class MainDashboard(discord.ui.View):

    def __init__(self, u_id, plan):

        super().__init__(timeout=300)

        self.u_id = str(u_id)

        self.plan = plan

        self.msg = None

        

        # If no plan, remove the management buttons

        if self.plan == "NONE":

            self.remove_item(self.create_slot)

            self.remove_item(self.edit_slots)



    @discord.ui.button(label="Redeem Key", style=discord.ButtonStyle.blurple, emoji="🔑")

    async def redeem(self, i, b):

        if str(i.user.id) != self.u_id: return

        modal = discord.ui.Modal(title="Redeem Key"); k = discord.ui.TextInput(label="Code"); modal.add_item(k)

        async def cb(it):

            async with aiosqlite.connect(DB_FILE) as db:

                async with db.execute("SELECT data FROM keys WHERE key_code = ?", (k.value.strip(),)) as cur:

                    row = await cur.fetchone()

                    if row:

                        p, d, s, c = row[0].split("|")

                        exp = (datetime.datetime.now() + datetime.timedelta(days=int(d))).isoformat()

                        await db.execute("UPDATE users SET plan=?, expiry=?, max_slots=?, max_camps=?, accepted_tos=1 WHERE user_id=?", (p, exp, int(s), int(c), self.u_id))

                        await db.execute("DELETE FROM keys WHERE key_code = ?", (k.value.strip(),))

                        await db.commit()

                        

                        # Refresh the view with all buttons enabled

                        new_view = MainDashboard(self.u_id, p)

                        new_view.msg = self.msg

                        await it.response.edit_message(embed=await get_dashboard_embed(self.u_id), view=new_view)

                    else: 

                        await it.response.send_message("❌ Invalid Key", ephemeral=True)

        modal.on_submit = cb; await i.response.send_modal(modal)



    @discord.ui.button(label="Create Slot", style=discord.ButtonStyle.green, emoji="➕")

    async def create_slot(self, i, b):

        if str(i.user.id) != self.u_id: return

        modal = discord.ui.Modal(title="New Slot Name"); n = discord.ui.TextInput(label="Name"); modal.add_item(n)

        async def cb(it):

            async with aiosqlite.connect(DB_FILE) as db:

                await db.execute("INSERT INTO slots (user_id, slot_name) VALUES (?,?)", (self.u_id, n.value))

                await db.commit()

            await it.response.edit_message(embed=await get_dashboard_embed(self.u_id), view=self)

        modal.on_submit = cb; await i.response.send_modal(modal)



    @discord.ui.button(label="Edit Slots", style=discord.ButtonStyle.gray, emoji="⚙️")

    async def edit_slots(self, i, b):

        if str(i.user.id) != self.u_id: return

        v = await EditSlotsView.create(self.u_id, self.msg)

        await i.response.edit_message(content="📂 Slots:", embed=None, view=v)



# --- [REMAINING CLASSES (SlotControlView, EditSlotsView, etc.) REMAIN THE SAME AS YOUR ORIGINAL CODE] ---

# (I am omitting them for brevity, but they work normally)

class CampaignManager(discord.ui.View):

    def __init__(self, u_id, s_name, c_name, p_msg):

        super().__init__(timeout=300); self.u_id, self.s_name, self.c_name, self.msg = u_id, s_name, c_name, p_msg



    @discord.ui.button(label="Pause/Resume", style=discord.ButtonStyle.blurple, emoji="⏯️")

    async def toggle(self, i, b):

        if str(i.user.id) != self.u_id: return

        async with aiosqlite.connect(DB_FILE) as db:

            await db.execute("UPDATE campaigns SET active = 1 - active WHERE user_id = ? AND camp_name = ?", (self.u_id, self.c_name))

            await db.commit()

        await i.response.send_message("✅ Status Updated", ephemeral=True)

        asyncio.create_task(campaign_loop(self.u_id, self.s_name, self.c_name))



    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, emoji="🗑️")

    async def delete(self, i, b):

        if str(i.user.id) != self.u_id: return

        async with aiosqlite.connect(DB_FILE) as db:

            await db.execute("DELETE FROM campaigns WHERE user_id = ? AND camp_name = ?", (self.u_id, self.c_name))

            await db.commit()

        await i.response.edit_message(content=f"Slot: **{self.s_name}**", view=SlotControlView(self.u_id, self.s_name, self.msg))



    @discord.ui.button(label="Back", style=discord.ButtonStyle.gray, emoji="🔙")

    async def back(self, i, b):

        await i.response.edit_message(content=f"Slot: **{self.s_name}**", view=SlotControlView(self.u_id, self.s_name, self.msg))



class SlotControlView(discord.ui.View):

    def __init__(self, u_id, s_name, p_msg):

        super().__init__(timeout=300); self.u_id, self.s_name, self.msg = u_id, s_name, p_msg



    @discord.ui.button(label="Set Token", style=discord.ButtonStyle.gray, emoji="🔑")

    async def set_token(self, i, b):

        if str(i.user.id) != self.u_id: return

        modal = discord.ui.Modal(title="Assign Token"); t = discord.ui.TextInput(label="Token"); modal.add_item(t)

        async def cb(it):

            async with aiosqlite.connect(DB_FILE) as db:

                await db.execute("UPDATE slots SET token = ? WHERE user_id = ? AND slot_name = ?", (t.value.strip(), self.u_id, self.s_name))

                await db.commit()

            await it.response.send_message("✅ Token Saved", ephemeral=True)

        modal.on_submit = cb; await i.response.send_modal(modal)



    @discord.ui.button(label="Set Campaign", style=discord.ButtonStyle.green, emoji="🚀")

    async def set_camp(self, i, b):

        if str(i.user.id) != self.u_id: return

        modal = discord.ui.Modal(title="New Campaign")

        n, c, m, d = discord.ui.TextInput(label="Name"), discord.ui.TextInput(label="Channels"), discord.ui.TextInput(label="Message", style=discord.TextStyle.paragraph), discord.ui.TextInput(label="Delay", default="60")

        for x in [n,c,m,d]: modal.add_item(x)

        async def cb(it):

            async with aiosqlite.connect(DB_FILE) as db:

                await db.execute("INSERT INTO campaigns VALUES (?,?,?,?,?,?,1)", (self.u_id, self.s_name, n.value, c.value, m.value, int(d.value)))

                await db.commit()

            asyncio.create_task(campaign_loop(self.u_id, self.s_name, n.value))

            await it.response.send_message("✅ Campaign Live", ephemeral=True)

        modal.on_submit = cb; await i.response.send_modal(modal)



    @discord.ui.button(label="Edit Campaign", style=discord.ButtonStyle.blurple, emoji="⚙️")

    async def edit_camp(self, i, b):

        if str(i.user.id) != self.u_id: return

        async with aiosqlite.connect(DB_FILE) as db:

            async with db.execute("SELECT camp_name FROM campaigns WHERE user_id = ? AND slot_name = ?", (self.u_id, self.s_name)) as cur:

                rows = await cur.fetchall()

        if not rows: return await i.response.send_message("No campaigns.", ephemeral=True)

        v = discord.ui.View()

        for r in rows:

            btn = discord.ui.Button(label=r[0], style=discord.ButtonStyle.gray)

            async def cb(it, name=r[0]): await it.response.edit_message(content=f"Managing: **{name}**", view=CampaignManager(self.u_id, self.s_name, name, self.msg))

            btn.callback = cb; v.add_item(btn)

        await i.response.edit_message(content="Select campaign:", view=v)



    @discord.ui.button(label="Back", style=discord.ButtonStyle.red, emoji="🔙")

    async def back(self, i, b):

        v = await EditSlotsView.create(self.u_id, self.msg)

        await i.response.edit_message(content="📂 Slots:", view=v)



class EditSlotsView(discord.ui.View):

    def __init__(self, u_id, p_msg):

        super().__init__(timeout=300); self.u_id, self.msg = u_id, p_msg

    @classmethod

    async def create(cls, u_id, p_msg):

        inst = cls(u_id, p_msg)

        async with aiosqlite.connect(DB_FILE) as db:

            async with db.execute("SELECT slot_name FROM slots WHERE user_id = ?", (u_id,)) as cur:

                async for r in cur:

                    btn = discord.ui.Button(label=f"📁 {r[0]}", style=discord.ButtonStyle.gray)

                    async def cb(i, n=r[0]): await i.response.edit_message(content=f"Slot: **{n}**", view=SlotControlView(u_id, n, p_msg))

                    btn.callback = cb; inst.add_item(btn)

        return inst

    @discord.ui.button(label="Main Menu", style=discord.ButtonStyle.red, row=4)

    async def back(self, i, b):

        user = await get_user_data(self.u_id)

        v = MainDashboard(self.u_id, user['plan']); v.msg = self.msg

        await i.response.edit_message(content=None, embed=await get_dashboard_embed(self.u_id), view=v)



# --- 6. BOT CORE ---

bot = commands.Bot(command_prefix="$", intents=discord.Intents.all(), help_command=None)



@bot.command()

async def panel(ctx):

    u_id = str(ctx.author.id)

    user = await get_user_data(u_id)

    v = MainDashboard(u_id, user['plan'])

    v.msg = await ctx.send(embed=await get_dashboard_embed(u_id), view=v)



@bot.command()

async def gen(ctx, tier: str):

    if ctx.author.id != OWNER_ID: return

    tiers = {"bronze1": ("BRONZE1", 7, 5, 50), "gold1": ("GOLD1", 7, 10, 60), "radiant": ("RADIANT", 30, 25, 100)}

    t = tiers.get(tier.lower())

    if not t: return await ctx.send("❌ bronze1, gold1, radiant")

    key = f"ELITE-{t[0]}-{''.join(random.choices(string.ascii_uppercase + string.digits, k=4))}"

    async with aiosqlite.connect(DB_FILE) as db:

        await db.execute("INSERT INTO keys VALUES (?,?,?)", (key, f"{t[0]}|{t[1]}|{t[2]}|{t[3]}", datetime.datetime.now().isoformat()))

        await db.commit()

    await ctx.send(f"🎫 Key: `{key}`")



@bot.event

async def on_ready():

    await init_db()

    print(f"🚀 EliteFlow Extreme Online")



bot.run('MTQ4MjM1NjM1MTA3MzEyODY2Mw.GKPPhZ.fUYBZUV0hDAe6CSA-M1LyWHFcsmzEC_jbNXGSI')

