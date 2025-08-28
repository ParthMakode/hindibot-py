# sound_scraper.py
import requests
from bs4 import BeautifulSoup
import os
from urllib.parse import quote_plus

BASE_URL = "https://www.myinstants.com"
# DOWNLOAD_DIR will be managed by the Discord bot, so it's not strictly needed here,
# but can be kept for consistency if this file might be run standalone.
# For the bot, we'll pass a specific download path.

def get_html_from_url(url):
  """
  Fetches the HTML content from a given URL.
  """
  try:
    response = requests.get(url)
    response.raise_for_status()
    return response.text
  except requests.exceptions.RequestException as e:
    print(f"Error fetching URL {url}: {e}")
    return None

def search_myinstants_sounds(query, num_results=3):
  """
  Searches Myinstants.com for sounds based on a query and returns a list
  of sound titles and their direct MP3 URLs, limited by num_results.
  """
  encoded_query = quote_plus(query)
  search_url = f"{BASE_URL}/en/search/?name={encoded_query}"

  # print(f"Searching Myinstants for: '{query}' at {search_url}") # Moved logging to Discord bot

  html_content = get_html_from_url(search_url)
  if not html_content:
    return []

  soup = BeautifulSoup(html_content, 'html.parser')
  sounds_found = []

  instants_container = soup.find('div', id='instants_container')

  if not instants_container:
      # print(f"Could not find the main 'instants_container' for query '{query}'. Website structure might have changed.")
      return []

  instant_divs = instants_container.find_all('div', class_='instant')

  if not instant_divs:
      # print(f"No individual instant sound buttons found within 'instants_container' for query '{query}'.")
      return []

  for instant_div in instant_divs:
    if len(sounds_found) >= num_results:
        break

    title_tag = instant_div.find('a', class_='instant-link')
    title = title_tag.get_text(strip=True) if title_tag else "Unknown Title"

    button_tag = instant_div.find('button', class_='small-button')
    mp3_url = None
    if button_tag and 'onclick' in button_tag.attrs:
      onclick_attr = button_tag['onclick']
      try:
        start_index = onclick_attr.find("play('") + len("play('")
        end_index = onclick_attr.find("'", start_index)
        relative_mp3_path = onclick_attr[start_index:end_index]
        mp3_url = BASE_URL + relative_mp3_path
      except Exception: # Removed detailed error logging here, Discord bot will handle it
        mp3_url = None
    
    if mp3_url:
      sounds_found.append({
          'title': title,
          'mp3_url': mp3_url
      })
  return sounds_found

def download_mp3(mp3_url, filename, save_dir): # filename is now mandatory
  """
  Downloads an MP3 file from a given URL.
  """
  if not mp3_url:
    # print("MP3 URL is empty, cannot download.") # Logging moved to Discord bot
    return None

  os.makedirs(save_dir, exist_ok=True)
  file_path = os.path.join(save_dir, filename)

  try:
    # print(f"Downloading '{filename}' from {mp3_url}...") # Logging moved
    response = requests.get(mp3_url, stream=True)
    response.raise_for_status()

    with open(file_path, 'wb') as f:
      for chunk in response.iter_content(chunk_size=8192):
        f.write(chunk)
    # print(f"Successfully downloaded to: {file_path}") # Logging moved
    return file_path
  except requests.exceptions.RequestException as e:
    # print(f"Error downloading {mp3_url}: {e}") # Logging moved
    return None
  except Exception as e:
    # print(f"An unexpected error occurred while downloading {mp3_url}: {e}") # Logging moved
    return None