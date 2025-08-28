import discord
from discord import app_commands
import os
from gtts import gTTS
import asyncio
from flask import Flask
from threading import Thread
import logging
import uuid
from scrape import search_myinstants_sounds, download_mp3
DOWNLOAD_DIR="./sounds/"
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
@app.route('/wakeup')
def wakeup():
    return "Server is awake and responding.", 200
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
        self.temp_sound_files = {} 

    async def setup_hook(self):
        # This syncs the commands to your specific guild.
        # For global commands, remove the guild argument.
        await self.tree.sync() # Use this for global commands
        logging.info("Commands synced successfully.")

# Define the necessary intents for the bot
intents = discord.Intents.default()
intents.guilds = True
intents.voice_states = True

# Instantiate the bot
bot = HindiBot(intents=intents)

# --- Custom View for Sound Buttons ---
class SoundButtonView(discord.ui.View):
    def __init__(self, downloaded_sounds_info: list, original_user_id: str):
        super().__init__(timeout=180) # Timeout after 3 minutes
        self.downloaded_sounds_info = downloaded_sounds_info # List of {'title': '...', 'path': '...'}
        self.original_user_id = original_user_id

        # Create a button for each sound
        for i, sound in enumerate(downloaded_sounds_info):
            # Custom IDs are required for persistent buttons.
            # We embed the index and a unique ID for the interaction to retrieve the sound path later.
            custom_id = f"play_sound_{sound['unique_id']}_{i}"
            self.add_item(discord.ui.Button(label=sound['title'], custom_id=custom_id, style=discord.ButtonStyle.primary))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Only allow the original user (or authorized user) to interact with these buttons
        if str(interaction.user.id) != self.original_user_id and str(interaction.user.id) != AUTHORIZED_USER_ID:
            await interaction.response.send_message("You are not authorized to use these buttons.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Stop All", style=discord.ButtonStyle.danger, custom_id="stop_all_sounds")
    async def stop_all_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        voice_client = discord.utils.get(bot.voice_clients, guild=interaction.guild)
        if voice_client and voice_client.is_playing():
            voice_client.stop()
            await interaction.response.send_message("Stopped current playback.", ephemeral=True)
        else:
            await interaction.response.send_message("No sound is currently playing.", ephemeral=True)

    @discord.ui.button(label="Disconnect", style=discord.ButtonStyle.red, custom_id="disconnect_bot")
    async def disconnect_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        voice_client = discord.utils.get(bot.voice_clients, guild=interaction.guild)
        if voice_client and voice_client.is_connected():
            await voice_client.disconnect()
            # Clean up associated temporary files
            self.cleanup_temp_files(interaction.message.id)
            await interaction.response.send_message("Disconnected from voice channel.", ephemeral=True)
            self.stop() # Stop the view as well
        else:
            await interaction.response.send_message("I'm not currently in a voice channel.", ephemeral=True)

    # This is a dynamic callback that will handle any button not explicitly defined
    # It catches all buttons whose custom_id starts with "play_sound_"
    async def on_timeout(self):
        # Remove the view when it times out
        if self.message:
            await self.message.edit(view=None)
        self.cleanup_temp_files(self.message.id) # Clean up on timeout

    # Placeholder for the message to edit later (set when the view is sent)
    message: discord.Message = None

    async def interaction_callback(self, interaction: discord.Interaction):
        if interaction.custom_id.startswith("play_sound_"):
            # Extract the index from the custom_id
            parts = interaction.custom_id.split('_')
            unique_id = parts[2]
            index = int(parts[3])

            if unique_id not in bot.temp_sound_files:
                await interaction.response.send_message("This interaction is too old or the sounds are no longer available.", ephemeral=True)
                return

            sound_info = bot.temp_sound_files[unique_id][index]
            sound_path = sound_info['path']
            sound_title = sound_info['title']

            # Check if user is in a voice channel
            if interaction.user.voice is None:
                await interaction.response.send_message("You need to be in a voice channel to play a sound.", ephemeral=True)
                return

            voice_channel = interaction.user.voice.channel
            voice_client = discord.utils.get(bot.voice_clients, guild=interaction.guild)

            # Connect or move to the user's voice channel
            try:
                if voice_client is None:
                    voice_client = await voice_channel.connect()
                elif voice_client.channel != voice_channel:
                    await voice_client.move_to(voice_channel)
            except asyncio.TimeoutError:
                await interaction.response.send_message("Failed to connect to the voice channel (timeout).", ephemeral=True)
                return
            except discord.ClientException as e:
                await interaction.response.send_message(f"Failed to connect to the voice channel: {e}", ephemeral=True)
                return

            # Defer the response as playing can take a moment
            await interaction.response.defer()

            # Play the audio
            if voice_client.is_playing():
                voice_client.stop()
            
            try:
                voice_client.play(discord.FFmpegPCMAudio(sound_path), after=lambda e: self.after_playback(e, sound_path))
                await interaction.followup.send(f"Playing: `{sound_title}`")
            except Exception as e:
                logging.error(f"Playback Error: {e}")
                await interaction.followup.send("An error occurred while trying to play the audio.")

    def cleanup_temp_files(self, message_id):
        # Clean up temporary files associated with this message/interaction
        unique_id_for_this_message = None
        for uid, sounds_list in bot.temp_sound_files.items():
            if sounds_list and sounds_list[0]['message_id'] == message_id:
                unique_id_for_this_message = uid
                break

        if unique_id_for_this_message:
            for sound_info in bot.temp_sound_files.pop(unique_id_for_this_message):
                if os.path.exists(sound_info['path']):
                    os.remove(sound_info['path'])
                    logging.info(f"Cleaned up temporary file: {sound_info['path']}")
            logging.info(f"Cleaned up all temporary files for message ID {message_id}")

    def after_playback(self, error, file_path):
        if error:
            logging.error(f"Playback error: {error}")
        # Optionally delete the file immediately after it finishes playing if needed
        # (Be careful with this if the bot might try to play it again quickly)
        # if os.path.exists(file_path):
        #     os.remove(file_path)
        #     logging.info(f"Deleted temporary file after playback: {file_path}")



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

# --- NEW COMMAND: /searchsound ---
@bot.tree.command(name="searchsound", description="Search Myinstants.com for sounds and play them in VC.")
@app_commands.describe(query="The sound you want to search for (e.g., 'oh my god', 'vine boom')")
async def searchsound(interaction: discord.Interaction, query: str):
    # 1. Authorization Check
    if str(interaction.user.id) != AUTHORIZED_USER_ID and str(interaction.user.id) != NAMIT_USER_ID:
        await interaction.response.send_message("You are not authorized to use this command.", ephemeral=True)
        return

    # Defer the response because search and download can take time
    await interaction.response.defer()

    num_to_download = 3
    # Use a unique ID for this specific interaction to manage temporary files
    interaction_unique_id = str(uuid.uuid4())
    temp_dir = os.path.join(DOWNLOAD_DIR, interaction_unique_id)
    os.makedirs(temp_dir, exist_ok=True)
    
    found_sounds = search_myinstants_sounds(query, num_results=num_to_download)
    downloaded_files_info = []

    if not found_sounds:
        await interaction.followup.send(f"No sounds found for '{query}'. Please try a different query.")
        # Clean up empty temp directory
        if os.path.exists(temp_dir) and not os.listdir(temp_dir):
            os.rmdir(temp_dir)
        return

    # 2. Download the top results
    for i, sound in enumerate(found_sounds):
        # Create a safe filename for the downloaded MP3
        safe_title = "".join(c if c.isalnum() or c in [' ', '_', '-'] else '' for c in sound['title']).strip()
        filename = f"{safe_title[:40].replace(' ', '_')}_{i+1}.mp3" # Limit title length for filename

        # Download the MP3
        logging.info(f"Attempting to download '{sound['title']}' to {os.path.join(temp_dir, filename)}")
        file_path = download_mp3(sound['mp3_url'], filename, temp_dir)

        if file_path:
            downloaded_files_info.append({
                'title': sound['title'],
                'path': file_path,
                'unique_id': interaction_unique_id, # Store this for button callback
                'message_id': None # Placeholder, will be filled after sending message
            })
        else:
            logging.warning(f"Failed to download sound: {sound['title']} from {sound['mp3_url']}")

    if not downloaded_files_info:
        await interaction.followup.send(f"Found sounds for '{query}', but failed to download any. Please try again later or with a different query.")
        # Clean up empty temp directory
        if os.path.exists(temp_dir) and not os.listdir(temp_dir):
            os.rmdir(temp_dir)
        return

    # Store the downloaded file info in the bot's temporary storage
    # This allows the button callback to retrieve the file path later
    bot.temp_sound_files[interaction_unique_id] = downloaded_files_info

    # 3. Create and send buttons
    view = SoundButtonView(downloaded_files_info, str(interaction.user.id))
    message_content = f"Here are the top {len(downloaded_files_info)} sounds for '{query}':"
    response_message = await interaction.followup.send(message_content, view=view)
    view.message = response_message # Store message reference for cleanup/timeout

    # Update message_id in stored sound info
    for sound in downloaded_files_info:
        sound['message_id'] = response_message.id

    # Schedule cleanup for temp files after the view times out or is explicitly stopped
    # The cleanup is handled by the View's on_timeout and disconnect_button now


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