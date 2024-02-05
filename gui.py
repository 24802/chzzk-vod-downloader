import sys
import re
import json
import requests
import xml.etree.ElementTree as ET
from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QLineEdit, QPushButton, QLabel, QProgressBar, QMessageBox, QLabel
from PyQt5.QtCore import QThread, pyqtSignal, Qt, QUrl
from PyQt5.QtGui import QDesktopServices
from streamlink import Streamlink
from tqdm import tqdm
from time import time

class DownloadThread(QThread):
    progress = pyqtSignal(int, str)
    completed = pyqtSignal(str)
    paused = pyqtSignal()
    stopped = pyqtSignal()

    def __init__(self, video_url, output_path):
        super().__init__()
        self.video_url = video_url
        self.output_path = output_path
        self._is_paused = False
        self._is_stopped = False

    def run(self):
        try:
            response = requests.get(self.video_url, stream=True)
            response.raise_for_status()

            total_size = int(response.headers.get('content-length', 0))
            downloaded_size = 0
            start_time = time()

            with open(self.output_path, 'wb') as f:
                while not self._is_stopped:
                    if self._is_paused:
                        self.paused.emit()
                        QThread.sleep(1)
                        continue

                    chunk = next(response.iter_content(chunk_size=8192), None)
                    if chunk:
                        f.write(chunk)
                        downloaded_size += len(chunk)
                        elapsed_time = time() - start_time
                        speed = downloaded_size / elapsed_time / 1024  # KB/s 단위로 속도 계산
                        progress = int((downloaded_size / total_size) * 100)
                        status_message = f"{downloaded_size / (1024 * 1024):.2f}MB/{total_size / (1024 * 1024):.2f}MB ({speed:.2f} KB/s)"
                        self.progress.emit(progress, status_message)

            self.stopped.emit() if self._is_stopped else self.completed.emit("다운로드 완료!")
        except requests.RequestException as e:
            self.completed.emit(f"다운로드 실패: {e}")
        except StopIteration:
            self.completed.emit("다운로드 완료!")

    def pause(self):
        self._is_paused = True

    def resume(self):
        self._is_paused = False
        self.paused.emit()

    def stop(self):
        self._is_stopped = True

class ChzzkStreamExtractor:
    VOD_URL = "https://apis.naver.com/neonplayer/vodplay/v2/playback/{video_id}?key={in_key}"
    VOD_INFO = "https://api.chzzk.naver.com/service/v2/videos/{video_no}"

    @staticmethod
    def extract_streams(link, cookies):
        # Initialize Streamlink session
        session = Streamlink()

        # Match the link to extract necessary information
        match = re.match(r'https?://chzzk\.naver\.com/(?:video/(?P<video_no>\d+)|live/(?P<channel_id>[^/?]+))$', link)
        if not match:
            print("Invalid link\n")
            return

        video_no = match.group("video_no")

        return ChzzkStreamExtractor._get_vod_streams(session, video_no, cookies)


    @staticmethod
    def download_video(video_url, output_path):
        try:
            response = requests.get(video_url, stream=True)
            response.raise_for_status()

            total_size = int(response.headers.get('content-length', 0))
            block_size = 8192
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

            root = ET.fromstring(response.text)
            ns = {"mpd": "urn:mpeg:dash:schema:mpd:2011", "nvod": "urn:naver:vod:2020"}

            base_url_element = root.find(".//mpd:BaseURL", namespaces=ns)

            if base_url_element is not None:
                return base_url_element.text
            else:
                print("BaseURL not found in DASH manifest\n")

        except requests.RequestException as e:
            print("Failed to fetch DASH manifest:", str(e), "\n")
        except ET.ParseError as e:
            print("Failed to parse DASH manifest XML:", str(e), "\n")

    @staticmethod
    def _get_vod_streams(session, video_no, cookies):
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

            if video_id is None or in_key is None and cookies:
                # 쿠키를 사용하여 재요청
                response = requests.get(api_url, cookies=cookies)
                response.raise_for_status()

                content = response.json().get('content', {})
                video_id = content.get('videoId')
                in_key = content.get('inKey')

            video_url = ChzzkStreamExtractor.VOD_URL.format(video_id=video_id, in_key=in_key)

            author = content.get('channel', {}).get('channelName')
            category = content.get('videoCategory')
            title = content.get('videoTitle')

            print(f"Author: {author}, Title: {title}, Category: {category}")

            base_url = ChzzkStreamExtractor._print_dash_manifest(video_url)

            if base_url:
                title = ChzzkStreamExtractor.clean_filename(title)
                output_path = f"{title}.mp4"
                return base_url, output_path, author, title, category

        except json.JSONDecodeError as e:
            print("Failed to decode JSON response:", str(e))
        return None, None, None, None, None

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
    @staticmethod
    def clean_filename(filename):
        cleaned_filename = re.sub(r'[♥♡ღ⭐㉦✧》《♠♦❤️♣✿ꈍᴗ\/@!~*\[\]\#\$\%\^\&\(\)\-\_\=\+\<\>\?\;\:\'\"]', '', filename)
        return cleaned_filename


class App(QWidget):
    def __init__(self):
        super().__init__()
        self.downloadThread = None
        self.initUI()

    def initUI(self):
        layout = QVBoxLayout()

        self.urlInput = QLineEdit(self)
        self.urlInput.setPlaceholderText("VOD URL을 입력하세요.")
        layout.addWidget(self.urlInput)

        self.nidAutInput = QLineEdit(self)
        self.nidAutInput.setPlaceholderText("NID_AUT 쿠키 값을 입력하세요.")
        layout.addWidget(self.nidAutInput)

        self.nidSesInput = QLineEdit(self)
        self.nidSesInput.setPlaceholderText("NID_SES 쿠키 값을 입력하세요.")
        layout.addWidget(self.nidSesInput)

        self.downloadButton = QPushButton('다운로드', self)
        self.downloadButton.clicked.connect(self.onDownload)
        layout.addWidget(self.downloadButton)

        self.pauseButton = QPushButton('일시정지', self)
        self.pauseButton.clicked.connect(self.onPause)
        self.pauseButton.setEnabled(False)
        layout.addWidget(self.pauseButton)

        self.stopButton = QPushButton('정지', self)
        self.stopButton.clicked.connect(self.onStop)
        self.stopButton.setEnabled(False)
        layout.addWidget(self.stopButton)

        self.infoLabel = QLabel('VOD 정보:', self)
        layout.addWidget(self.infoLabel)

        self.progressLabel = QLabel('상태:', self)
        layout.addWidget(self.progressLabel)

        self.progressBar = QProgressBar(self)
        layout.addWidget(self.progressBar)

        self.discordLink = QLabel('<a href="https://discord.com/users/245702966085025802">Discord</a>', self)
        self.discordLink.setOpenExternalLinks(True)  # 외부 링크 열기 활성화
        self.discordLink.setAlignment(Qt.AlignRight | Qt.AlignBottom)  # 우측 하단 정렬
        layout.addWidget(self.discordLink)

        self.setLayout(layout)

        self.setLayout(layout)
        self.setWindowTitle('치지직 VOD 다운로더')
        self.setGeometry(300, 300, 300, 150)

    def onDownload(self):
        link = self.urlInput.text()
        cookies = {
            'NID_AUT': self.nidAutInput.text(),
            'NID_SES': self.nidSesInput.text()
        }

        video_url, output_path, author, title, category = ChzzkStreamExtractor.extract_streams(link, cookies)
        if video_url is None:
            QMessageBox.critical(self, "Error", "유효한 비디오 링크가 아니거나 로그인 쿠키의 오류가 발생했습니다.")
            return

        if video_url and output_path:
            self.infoLabel.setText(f"<b>스트리머:</b> {author}<br><b>제목:</b> {title}<br><b>카테고리:</b> {category}\n")
            self.downloadThread = DownloadThread(video_url, output_path)
            self.downloadThread.progress.connect(self.updateProgress)
            self.downloadThread.completed.connect(self.onDownloadCompleted)
            self.downloadThread.start()

            self.pauseButton.setEnabled(True)
            self.stopButton.setEnabled(True)
            self.downloadButton.setEnabled(False)

    def onPause(self):
        if self.downloadThread and self.downloadThread.isRunning():
            if self.downloadThread._is_paused:
                self.downloadThread.resume()
                self.pauseButton.setText('일시정지')  # 다운로드 재개 상태에서의 버튼 텍스트
            else:
                self.downloadThread.pause()
                self.pauseButton.setText('이어서 다운로드')  # 일시정지 상태에서의 버튼 텍스트

    def onStop(self):
        if self.downloadThread and self.downloadThread.isRunning():
            self.downloadThread.stop()
            self.downloadButton.setEnabled(True)
            self.pauseButton.setEnabled(False)
            self.stopButton.setEnabled(False)

    def updateProgress(self, value, status_message):
        self.progressBar.setValue(value)
        self.progressLabel.setText(status_message)

    def onDownloadCompleted(self, message):
        self.progressLabel.setText(message)
        self.downloadButton.setEnabled(True)
        self.pauseButton.setEnabled(False)
        self.stopButton.setEnabled(False)

if __name__ == '__main__':
    app = QApplication(sys.argv)
    ex = App()
    ex.show()
    sys.exit(app.exec_())
