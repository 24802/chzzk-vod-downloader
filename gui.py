import sys
import re
import json
import requests
import xml.etree.ElementTree as ET
from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QLineEdit, QPushButton, QLabel, QProgressBar, QMessageBox, QLabel, QHBoxLayout
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
            self.completed.emit(f"다운로드 완료: {e}")
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
            QMessageBox.critical(None, "Error", "유효한 비디오 링크가 아닙니다.")
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
            QMessageBox.information(None, "Information", "다운로드 완료!")

        except requests.RequestException as e:
            QMessageBox.critical(None, "Error", f"다운로드 실패: {e}")

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
                QMessageBox.critical(None, "Error", "DASH 매니페스트에서 BaseURL을 찾을 수 없습니다.")

        except requests.RequestException as e:
            QMessageBox.critical(None, "Error", f"DASH 매니페스트를 가져오는데 실패했습니다:\n{e}")
        except ET.ParseError as e:
            QMessageBox.critical(None, "Error", f"DASH 매니페스트 XML 파싱에 실패했습니다:\n{e}")

    @staticmethod
    def _get_vod_streams(session, video_no, cookies):
        api_url = ChzzkStreamExtractor.VOD_INFO.format(video_no=video_no)
        UserAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"
        try:
            response = requests.get(api_url, headers={"User-Agent": UserAgent})
            response.raise_for_status()
        except requests.RequestException as e:
            QMessageBox.critical(None, "Error", f"비디오 정보를 가져오는데 실패했습니다:\n{e}")
            return

        if response.status_code == 404:
            QMessageBox.critical(None, "Error", "비디오 정보를 찾을 수 없습니다.")
            return

        try:
            content = response.json().get('content', {})
            video_id = content.get('videoId')
            in_key = content.get('inKey')

            if video_id is None or in_key is None and cookies:
                # 쿠키를 사용하여 재요청
                response = requests.get(api_url, cookies=cookies, headers={"User-Agent": UserAgent})
                response.raise_for_status()

                content = response.json().get('content', {})
                video_id = content.get('videoId')
                in_key = content.get('inKey')

            video_url = ChzzkStreamExtractor.VOD_URL.format(video_id=video_id, in_key=in_key)

            author = content.get('channel', {}).get('channelName')
            category = content.get('videoCategory')
            title = content.get('videoTitle')

            base_url = ChzzkStreamExtractor._print_dash_manifest(video_url)

            if base_url:
                title = ChzzkStreamExtractor.clean_filename(title)
                output_path = f"{title}.mp4"
                return base_url, output_path, author, title, category

        except json.JSONDecodeError as e:
            QMessageBox.critical(None, "Error", f"비디오 정보 JSON 디코딩에 실패했습니다:\n{e}")
        return None, None, None, None, None

    @staticmethod
    def _load_cookies_from_file(file_path):
        try:
            with open(file_path, 'r') as file:
                cookies = json.load(file)
            return cookies
        except FileNotFoundError:
            QMessageBox.critical(None, "Error", f"쿠키 파일을 찾을 수 없습니다: {file_path}")
            return None
        except json.JSONDecodeError:
            QMessageBox.critical(None, "Error", f"쿠키 파일을 디코딩하는데 실패했습니다: {file_path}")
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

        linksLayout = QHBoxLayout()

        # 사용법 링크 라벨 설정
        self.usageLink = QLabel('<a href="usage">사용법</a>', self)
        self.usageLink.setOpenExternalLinks(False)
        self.usageLink.linkActivated.connect(self.showReadMe)

        # Discord 링크 라벨 설정
        self.discordLink = QLabel('<a href="https://discord.com/users/245702966085025802">Discord</a>', self)
        self.discordLink.setOpenExternalLinks(True)

        # linksLayout에 위젯 추가하고, 스트레치를 이용하여 양 끝으로 링크를 배치
        linksLayout.addWidget(self.usageLink)
        linksLayout.addStretch(1)
        linksLayout.addWidget(self.discordLink)

        # 메인 레이아웃에 링크 레이아웃을 추가
        layout.addLayout(linksLayout)

        self.setLayout(layout)
        self.setWindowTitle('치지직 VOD 다운로더')
        self.setGeometry(300, 300, 300, 150)
    def showReadMe(self, link):
        readMeText = """1. VOD URL을 입력하세요.
        
2. 만약 연령 제한 VOD일 경우 NID_AUT, NID_SES 쿠키 값을 입력하세요.

3. 다운로드 버튼을 클릭하세요.

4. 일시정지/정지 버튼을 사용하여 다운로드를 일시정지/정지할 수 있습니다."""
        QMessageBox.information(self, "사용법", readMeText)

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
