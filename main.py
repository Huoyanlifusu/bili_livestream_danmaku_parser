import requests
from util import baseUrl, webUrl, roomUrl, headers, params, data, params_dminfo, myUid
from util import sleep
from util import heartbeat_body
from node import add_comment, cmd_analyze, singleton
from log  import logger
import threading
from collections import deque
import asyncio
import json
from proto import Proto
import websockets

class Command():
    def __init__(self, time, uid, text):
        self.uid = uid
        self.time = time
        self.text = text

# https访问弹幕去重
class Deduper:
    def __init__(self, max_size: int = 1000):
        self.max_size = max_size
        self.seen = set()
        self.queue = deque()
        self.lock = threading.Lock()

    def _make_key(self, com: dict):
        return (com.get('uid'), com.get('text'))

    def is_duplicate(self, com: dict) -> bool:
        key = self._make_key(com)
        with self.lock:
            if key in self.seen:
                return True
            # new item -> add to seen and queue, evict if necessary
            self.seen.add(key)
            self.queue.append(key)
            if len(self.queue) > self.max_size:
                old = self.queue.popleft()
                self.seen.discard(old)
            return False

# customize maximum size for deduplication
deduper = Deduper(max_size=2000)

@staticmethod
def catch_live_comment_html(url, headers, data):
    html = requests.post(url=url, headers=headers, data=data)
    if html.status_code != 200:
        logger.pr_error(f"HTTP request failed towards {url} with status code {html.status_code}")
        return None

    return html

@singleton
class BiliStreamClient():
    def __init__(self):
        self.room_id = 0
        self.websocket = None
        self.roomUrl = roomUrl
        self.webUrl = webUrl
        self.roomHeaders = headers
        self.roomParams = params
        self.webHeaders = headers
        self.webParams = params_dminfo
        self.heartbeat_interval = 30  # default heartbeat interval in seconds
        self.heartbeat_body = heartbeat_body
        self.token = ""
        self.hosts = []

    async def fetch_room_id(self):
        try:
            html = requests.get(url=self.roomUrl, params=self.roomParams, headers=self.roomHeaders)
        except requests.RequestException as e:
            logger.pr_error(f"HTTP request exception towards {self.roomUrl}: {e}")
            return
        if html.status_code != 200:
            logger.pr_error(f"HTTP request failed towards {self.roomUrl} with status code {html.status_code}")
            return
        if html.json().get('data', -1) == {}:
            logger.pr_error(f"API returned empty data from {self.roomUrl}")
            return
        self.room_id = html.json().get('data', {}).get('room_id', -1)
        if self.room_id == -1:
            logger.pr_error(f"Failed to fetch room_id from {self.roomUrl}")
            return
        logger.pr_debug(f"successfully fetched room_id {self.room_id} from {self.roomUrl}")

    async def access_bili_websocket_html(self):
        try:
            html = requests.get(url=self.webUrl, params=self.webParams, headers=self.webHeaders)
        except requests.RequestException as e:
            logger.pr_error(f"HTTP request exception towards {self.webUrl}: {e}")
            return

        if html.status_code != 200:
            logger.pr_error(f"HTTP request failed towards {self.webUrl} with status code {html.status_code}")
            return

        if html.json().get('code', -1) != 0:
            logger.pr_error(f"API returned error code {html.json().get('code', -1)} from {self.webUrl}")
            logger.pr_error(f"Possible reasons for err code -352: invalid parameters or access restrictions.")
            return

        logger.pr_debug(f"successfully accessed websocket info from {self.webUrl}")
        self.token = html.json().get('data', {}).get('token', '')
        self.hosts = html.json().get('data', {}).get('host_list', [])
    
    async def build_auth_packet(self):
        auth_packet = Proto()
        auth_packet.ver = 1
        auth_packet.op = 7
        auth_packet.seq = 1
        auth_packet.body = json.dumps({
            "uid": myUid, 
            "roomid": self.room_id,
            "protover": 3,
            "buvid": "895976D6-ACF1-0AAE-93FD-335D6F10128823063infoc",
            "support_ack": True,
            "queue_uuid": "g0myt1hu",
            "scene": "room",
            "platform": "web",
            "type": 2,
            "key": self.token
        })
        packet = auth_packet.pack()
        return packet

    async def heartbeat_packet(self):
        heartbeart_proto = Proto()
        heartbeart_proto.ver = 1
        heartbeart_proto.op = 2
        heartbeart_proto.seq = 1
        heartbeart_proto.body = '[object Object]'
        packet = heartbeart_proto.pack()
        return packet
    
    async def send_heartbeat(self):
        try:
            while True:
                logger.pr_debug("Sent heartbeat packet to server")
                heartbeat_pkt = await self.heartbeat_packet()
                await self.websocket.send(heartbeat_pkt)
                await asyncio.sleep(self.heartbeat_interval)
        except websockets.exceptions.ConnectionClosed:
            logger.pr_debug("send_heartbeat: connection closed, stopping heartbeat task")
        except Exception as e:
            logger.pr_error(f"send_heartbeat: unexpected error: {e}")
    
    async def fetch_and_process_comments(self):
        try:
            while True:
                data = await self.websocket.recv()
                logger.pr_debug(f"Received WebSocket data: {data}")
        except websockets.exceptions.ConnectionClosed as e:
            logger.pr_info(f"fetch_and_process_comments: connection closed: {e}")
        except Exception as e:
            logger.pr_error(f"fetch_and_process_comments: unexpected error: {e}")

    async def connect_to_host(self):
        hostidx = 1
        auth_packet = await self.build_auth_packet()
        url = f'wss://{self.hosts[hostidx]["host"]}:{self.hosts[hostidx]["wss_port"]}/sub'

        try:
            self.websocket = await websockets.connect(url)
        except Exception as e:
            logger.pr_error(f"Failed to connect to WebSocket URL {url}: {e}")
            await self.reconnect()
            return

        logger.pr_info(f"Connected to WebSocket at {url}")

        try:
            await self.websocket.send(auth_packet)
        except Exception as e:
            logger.pr_error(f"Failed to send auth packet: {e}")
            await self.websocket.close()
            await self.reconnect()
            return

        # asyncio.create_task(self.send_heartbeat())
        await self.websocket.send("heartbeat")

        await self.fetch_and_process_comments()

    async def reconnect(self):
        await asyncio.sleep(5)  # wait before reconnecting
        await self.connect_to_host()

def catch_with_https():
    while True:
        catch_with_https_debug()

@staticmethod
def catch_with_https_debug():
    html = catch_live_comment_html(baseUrl, headers, data)
    if not html:
        return
    try:
        payload = html.json()
    except ValueError:
        logger.pr_error("Invalid JSON response in debug fetch")
        return

    for com_li in payload.get('data', {}).get('room', []):
        if not com_li:
            logger.pr_info("fetch null command")
            continue

        if deduper.is_duplicate(com_li):
            logger.pr_debug(f"duplicate skipped: uid={com_li.get('uid')} text={com_li.get('text')}")
            continue

        cmd = Command(com_li.get('timeline'), com_li.get('uid'), com_li.get('text'))
        add_comment(cmd)

    sleep()

def debug_mode():
    try:
        bsclient = BiliStreamClient()
        asyncio.run(bsclient.fetch_room_id())
        if bsclient.room_id == 0 or bsclient.room_id == -1:
            logger.pr_error("Failed to fetch valid room_id")
            return
        
        asyncio.run(bsclient.access_bili_websocket_html())
        if not bsclient.token or not bsclient.hosts:
            logger.pr_error("Failed to access BiliBili WebSocket info")
            return
    
        asyncio.run(bsclient.connect_to_host())

    except Exception as e:
        logger.pr_error(f"Error in debug mode: {e}")

if __name__ == "__main__":

    if 0:
        # https protocol
        thread_c = threading.Thread(target=catch_with_https, daemon=True)
        thread_m = threading.Thread(target=cmd_analyze, daemon=True)

        thread_c.start()
        thread_m.start()

        thread_c.join()
        thread_m.join()

    # websocket protocol
    debug_mode()
