import discord
from discord import app_commands
import os
from gtts import gTTS
import asyncio
from flask import Flask
from threading import Thread
import logging

# --- Basic Setup ---
# It's recommended to use a logger for better debugging, especially on a server
logging.basicConfig(level=logging.INFO)

# Load environment variables
try:
    TOKEN = os.environ['DISTOKEN']
    AUTHORIZED_USER_ID = os.environ['AUTHORIZED_USER_ID']
except KeyError as e:
    logging.error(f"CRITICAL: Environment variable {e} not found. Please set it before running.")
    exit()

NAMIT_USER_ID = "690196843929403653"
CLIENT_ID = "1227213236747898970" # While not used in the logic, kept for reference

# --- Flask Web Server for Render Cold Start ---
# This simple web server responds to HTTP requests, which keeps the Render service "warm".
app = Flask('')

@app.route('/')
def home():
    return "HindiBot is alive!"

def run_flask():
    # Use the PORT environment variable provided by Render, default to 8080
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

def start_server_thread():
    server_thread = Thread(target=run_flask)
    server_thread.daemon = True
    server_thread.start()
    logging.info("Flask server started in a background thread.")

# --- Discord Bot Setup ---
class HindiBot(discord.Client):
    def __init__(self, *, intents: discord.Intents):
        super().__init__(intents=intents)
        # CommandTree holds all the application commands
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        # This syncs the commands to your specific guild.
        # For global commands, remove the guild argument.
        # await self.tree.sync() # Use this for global commands
        logging.info("Commands synced successfully.")

# Define the necessary intents for the bot
intents = discord.Intents.default()
intents.guilds = True
intents.voice_states = True

# Instantiate the bot
bot = HindiBot(intents=intents)

@bot.event
async def on_ready():
    logging.info(f'Logged in as {bot.user} (ID: {bot.user.id})')
    logging.info('------')
    # Sync commands after the bot is ready
    await bot.tree.sync()


# --- Bot Commands ---

@bot.tree.command(name="hin", description="Make the bot speak text in a voice channel")
@app_commands.describe(text="The text you want the bot to speak (or 'exit' to leave)")
async def hin(interaction: discord.Interaction, text: str):
    # 1. Authorization Check
    if str(interaction.user.id) != AUTHORIZED_USER_ID:
        await interaction.response.send_message("You are not authorized to use this command.", ephemeral=True)
        return

    # 2. Check if user is in a voice channel
    if interaction.user.voice is None:
        await interaction.response.send_message("You need to be in a voice channel to use this command.", ephemeral=True)
        return

    voice_channel = interaction.user.voice.channel
    voice_client = discord.utils.get(bot.voice_clients, guild=interaction.guild)

    # 3. Handle 'exit' command
    if text.lower() == "exit":
        if voice_client and voice_client.is_connected():
            await voice_client.disconnect()
            await interaction.response.send_message("Disconnected from the voice channel.", ephemeral=True)
        else:
            await interaction.response.send_message("I'm not currently in a voice channel.", ephemeral=True)
        return

    # 4. Connect or move to the user's voice channel
    if voice_client is None:
        voice_client = await voice_channel.connect()
    elif voice_client.channel != voice_channel:
        await voice_client.move_to(voice_channel)

    # Defer the response as generating audio can take time
    await interaction.response.defer()

    # 5. Handle special audio files
    special_files = {
        "mew": "mew.mp3",
        "gyatt": "gyatt.mp3",
        "humi": "humi.mp3",
        "mcstan": "tmkc_mcstan.mp3"
    }

    source_file = special_files.get(text.lower())

    # 6. Generate TTS if not a special file
    if source_file is None:
        try:
            tts = gTTS(text=text, lang='hi')
            source_file = "tts.mp3"
            tts.save(source_file)
        except Exception as e:
            logging.error(f"gTTS Error: {e}")
            await interaction.followup.send("An error occurred while generating the TTS audio.")
            return

    # 7. Play the audio
    if voice_client.is_playing():
        voice_client.stop()
    
    try:
        voice_client.play(discord.FFmpegPCMAudio(source_file))
        await interaction.followup.send(f"Speaking: `{text}`")
    except Exception as e:
        logging.error(f"Playback Error: {e}")
        await interaction.followup.send("An error occurred while trying to play the audio.")


@bot.tree.command(name="namit", description="Make the bot speak text for Namit")
@app_commands.describe(text="The text Namit wants the bot to speak")
async def namit(interaction: discord.Interaction, text: str):
    # 1. Authorization Check
    if str(interaction.user.id) != NAMIT_USER_ID and str(interaction.user.id) != AUTHORIZED_USER_ID:
        await interaction.response.send_message("You are not Namit. WHO ARE YOU IMPOSTER !!??!!", ephemeral=True)
        return
        
    # Logic is nearly identical to /hin, so we can reuse most of it
    if interaction.user.voice is None:
        await interaction.response.send_message("You need to be in a voice channel to use this command.", ephemeral=True)
        return

    voice_channel = interaction.user.voice.channel
    voice_client = discord.utils.get(bot.voice_clients, guild=interaction.guild)

    if text.lower() == "exit":
        if voice_client and voice_client.is_connected():
            await voice_client.disconnect()
            await interaction.response.send_message("Disconnected from the voice channel.", ephemeral=True)
        else:
            await interaction.response.send_message("I'm not currently in a voice channel.", ephemeral=True)
        return

    if voice_client is None:
        voice_client = await voice_channel.connect()
    elif voice_client.channel != voice_channel:
        await voice_client.move_to(voice_channel)

    await interaction.response.defer()

    # The only difference for Namit is the text spoken
    spoken_text = f"{text}, said Namit."
    
    try:
        tts = gTTS(text=spoken_text, lang='hi')
        source_file = "tts_namit.mp3"
        tts.save(source_file)
    except Exception as e:
        logging.error(f"gTTS Error: {e}")
        await interaction.followup.send("An error occurred while generating the TTS audio.")
        return

    if voice_client.is_playing():
        voice_client.stop()

    try:
        voice_client.play(discord.FFmpegPCMAudio(source_file))
        await interaction.followup.send(f"Speaking: `{spoken_text}`")
    except Exception as e:
        logging.error(f"Playback Error: {e}")
        await interaction.followup.send("An error occurred while trying to play the audio.")


# --- Main Execution ---
if __name__ == "__main__":
    # Start the Flask server in a separate thread to handle web requests
    start_server_thread()
    
    # Start the Discord bot in the main thread
    try:
        bot.run(TOKEN)
    except discord.errors.LoginFailure:
        logging.error("CRITICAL: Login failed. Is the DISCORD_TOKEN correct?")
    except Exception as e:
        logging.error(f"An unexpected error occurred while running the bot: {e}")