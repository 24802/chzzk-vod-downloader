import re
import json
import requests
from streamlink import Streamlink
import xml.etree.ElementTree as ET
from tqdm import tqdm
import ctypes

ctypes.windll.kernel32.SetConsoleTitleW("CHZZK VOD Downloader")

class ChzzkStreamExtractor:
    VOD_URL = "https://apis.naver.com/neonplayer/vodplay/v2/playback/{video_id}?key={in_key}"
    VOD_INFO = "https://api.chzzk.naver.com/service/v2/videos/{video_no}"

    @staticmethod
    def extract_streams(link):
        # Initialize Streamlink session
        session = Streamlink()

        # Match the link to extract necessary information
        match = re.match(r'https?://chzzk\.naver\.com/(?:video/(?P<video_no>\d+)|live/(?P<channel_id>[^/?]+))$', link)
        if not match:
            print("Invalid link\n")
            return

        video_no = match.group("video_no")

        return ChzzkStreamExtractor._get_vod_streams(session, video_no)

    @staticmethod
    def download_video(video_url, output_path):
        try:
            response = requests.get(video_url, stream=True)
            response.raise_for_status()

            total_size = int(response.headers.get('content-length', 0))
            block_size = 8192  # Adjust as needed
            progress_bar = tqdm(total=total_size, unit='B', unit_scale=True)

            with open(output_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=block_size):
                    if chunk:
                        f.write(chunk)
                        progress_bar.update(len(chunk))

            progress_bar.close()  # Close tqdm when done
            print("Download completed!\n")

        except requests.RequestException as e:
            print("Failed to download video:", str(e), "\n")

    @staticmethod
    def _print_dash_manifest(video_url):
        try:
            response = requests.get(video_url, headers={"Accept": "application/dash+xml"})
            response.raise_for_status()

            # Parse DASH manifest XML with namespaces
            root = ET.fromstring(response.text)
            ns = {"mpd": "urn:mpeg:dash:schema:mpd:2011", "nvod": "urn:naver:vod:2020"}

            # Find BaseURL using find method with namespaces
            base_url_element = root.find(".//mpd:BaseURL", namespaces=ns)

            # Print the BaseURL if found
            if base_url_element is not None:
                return base_url_element.text
            else:
                print("BaseURL not found in DASH manifest\n")

        except requests.RequestException as e:
            print("Failed to fetch DASH manifest:", str(e), "\n")
        except ET.ParseError as e:
            print("Failed to parse DASH manifest XML:", str(e), "\n")

    @staticmethod
    def _get_vod_streams(session, video_no):
        api_url = ChzzkStreamExtractor.VOD_INFO.format(video_no=video_no)

        try:
            response = requests.get(api_url)
            response.raise_for_status()
        except requests.RequestException as e:
            print("Failed to fetch video information:", str(e), "\n")
            return

        if response.status_code == 404:
            print("Video not found\n")
            return

        try:
            content = response.json().get('content', {})
            video_id = content.get('videoId')
            in_key = content.get('inKey')

            # Check if videoId and inKey are None
            if video_id is None or in_key is None:

                print("This is a need to login video.", "\n")
                # Load NID_AUT and NID_SES from cookies.json file
                cookies = ChzzkStreamExtractor._load_cookies_from_file("cookies.json")
                if cookies is not None:
                    # Retry the request with cookies
                    response = requests.get(api_url, cookies=cookies)
                    response.raise_for_status()

                    # Update video_id and in_key with the new values
                    content = response.json().get('content', {})
                    video_id = content.get('videoId')
                    in_key = content.get('inKey')

            video_url = ChzzkStreamExtractor.VOD_URL.format(video_id=video_id, in_key=in_key)

            author = content.get('channel', {}).get('channelName')
            category = content.get('videoCategory')
            title = content.get('videoTitle')

            print(f"Author: {author}, Title: {title}, Category: {category}")

            # Print DASH manifest data
            base_url = ChzzkStreamExtractor._print_dash_manifest(video_url)

            # Ask user if they want to download the video
            if base_url:
                output_path = f"{title}.mp4"
                ChzzkStreamExtractor.download_video(base_url, output_path)

        except json.JSONDecodeError as e:
            print("Failed to decode JSON response:", str(e))

    @staticmethod
    def _load_cookies_from_file(file_path):
        try:
            with open(file_path, 'r') as file:
                cookies = json.load(file)
            return cookies
        except FileNotFoundError:
            print(f"Cookie file not found: {file_path}", "\n")
            return None
        except json.JSONDecodeError:
            print(f"Error decoding JSON from file: {file_path}", "\n")
            return None

if __name__ == "__main__":
    while True:
        # Get the link from the user
        link = input("Enter the link (or type 'exit' to quit): ")

        if link.lower() == 'exit':
            break

        # Extract streams from the link
        ChzzkStreamExtractor.extract_streams(link)
