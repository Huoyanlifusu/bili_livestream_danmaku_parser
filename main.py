import requests
from util import baseUrl, webUrl, roomUrl, headers, params, data, myUid
from util import sleep
from util import heartbeat_body
from node import add_comment, cmd_analyze
from log  import logger
import threading
from collections import deque
import asyncio
import json
from proto import Proto
import websockets
import time
from key import _WbiSigner
import weakref
import aiohttp

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

_session_to_wbi_signer = weakref.WeakKeyDictionary()
def _get_wbi_signer(session: aiohttp.ClientSession) -> '_WbiSigner':
    wbi_signer = _session_to_wbi_signer.get(session, None)
    if wbi_signer is None:
        wbi_signer = _session_to_wbi_signer[session] = _WbiSigner(session)
    return wbi_signer

class BiliStreamClient():
    def __init__(self):
        self.room_id = 0
        self.websocket = None
        self.webUrl = webUrl
        self.heartbeat_interval = 30  # default heartbeat interval in seconds
        self.heartbeat_body = heartbeat_body
        self.token = ""
        self.hosts = []
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        self._wbi_signer = _get_wbi_signer(self._session)

    async def fetch_room_id(self):
        try:
            html = requests.get(url=roomUrl, params=params, headers=headers)
        except requests.RequestException as e:
            logger.pr_error(f"HTTP request exception towards {roomUrl}: {e}")
            return
        
        if html.status_code != 200:
            logger.pr_error(f"HTTP request failed towards {roomUrl} with status code {html.status_code}")
            return
        
        if html.json().get('data', -1) == {}:
            logger.pr_error(f"API returned empty data from {roomUrl}")
            return
        
        self.room_id = html.json().get('data', {}).get('room_id', -1)
        if self.room_id == -1:
            logger.pr_error(f"Failed to fetch room_id from {roomUrl}")
            return
        
        logger.pr_debug(f"successfully fetched room_id {self.room_id} from {roomUrl}")

    async def access_bili_websocket_html(self):
        if self._wbi_signer.need_refresh_wbi_key:
            await self._wbi_signer.refresh_wbi_key()
            # 如果没刷新成功先用旧的key
            if self._wbi_signer.wbi_key == '':
                logger.pr_error(f"room={self.room_id} _init_host_server() failed: no wbi key")
                return
        params = self._wbi_signer.add_wbi_sign({
            'id': self.room_id,
            'type': 0,
            'web_location': "444.8"
        })
        try:
            html = requests.get(url=self.webUrl, params=params, headers=headers)
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
        auth_json = json.dumps({
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
        auth_packet.body = auth_json
        # 保存最近一次 auth body 以便日志记录
        self._last_auth_body = auth_json
        packet = auth_packet.pack()

        return packet

    async def heartbeat_packet(self):
        heartbeart_proto = Proto()
        heartbeart_proto.ver = 1
        heartbeart_proto.op = 2
        heartbeart_proto.seq = 1
        # Heartbeat should be an empty body for this server (header-only packet)
        heartbeart_proto.body = ''
        packet = heartbeart_proto.pack()
        return packet
    
    async def send_heartbeat(self):
        try:
            while True:
                await asyncio.sleep(self.heartbeat_interval)
                if self.websocket is None:
                    logger.pr_debug("WebSocket is None in send_heartbeat, stopping")
                    break
                
                try:
                    heartbeat_pkt = await self.heartbeat_packet()
                    await self.websocket.send(heartbeat_pkt)
                    logger.pr_debug("Sent heartbeat packet to server")
                except websockets.exceptions.ConnectionClosed:
                    logger.pr_debug("send_heartbeat: connection closed, stopping heartbeat task")
                    break
                except Exception as e:
                    logger.pr_error(f"send_heartbeat: unexpected error: {e}")
        except Exception as e:
            logger.pr_error(f"send_heartbeat: unexpected error: {e}")

    async def _send_heartbeat(self):
        if self.websocket is None:
            logger.pr_debug("WebSocket is None in _send_heartbeat")
            return

        try:
            heartbeat_pkt = await self.heartbeat_packet()
            await self.websocket.send(heartbeat_pkt)
            logger.pr_debug("Sent heartbeat packet to server")
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
        # 遍历返回的 host_list，逐个尝试连接并鉴权
        while True:
            host = self.hosts[0].get('host')
            port = self.hosts[0].get('wss_port')
            url = f'wss://{host}:{port}/sub'

            try:
                self.websocket = await websockets.connect(url)
                logger.pr_info(f"Connected to WebSocket at {url}")
                
                # 构建并发送 auth 包
                auth_packet = await self.build_auth_packet()
                await self.websocket.send(auth_packet)
                logger.pr_debug("Sent authentication packet")
                
                # 创建心跳和接收任务
                hb_task = asyncio.create_task(self.send_heartbeat())
                recv_task = asyncio.create_task(self.fetch_and_process_comments())

                # 等待任意一个任务完成（通常是接收任务因连接关闭）
                done, pending = await asyncio.wait([hb_task, recv_task], return_when=asyncio.FIRST_COMPLETED)
                
                # 取消剩余的任务
                for task in pending:
                    task.cancel()
                
                logger.pr_info("Connection lost, attempting to reconnect in 5 seconds...")
                await asyncio.sleep(5)
                
            except Exception as e:
                logger.pr_error(f"Failed to connect to WebSocket URL {url}: {e}")
                await asyncio.sleep(5)

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

async def debug_mode_async():
    bsclient = BiliStreamClient()

    await bsclient.fetch_room_id()
    if bsclient.room_id == 0 or bsclient.room_id == -1:
        logger.pr_error("Failed to fetch valid room_id")
        return

    await bsclient.access_bili_websocket_html()
    if not bsclient.token or not bsclient.hosts:
        logger.pr_error("Failed to access BiliBili WebSocket info")
        return

    await bsclient.connect_to_host()

def debug_mode():
    try:
        asyncio.run(debug_mode_async())
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
