from typing import NamedTuple, Union, Optional
import requests
from util import baseUrl, webUrl, roomUrl, headers, params, data, myUid
from util import sleep
from util import heartbeat_body
from util import UID_INIT_URL, USER_AGENT, BUVID_INIT_URL
from node import add_comment, cmd_analyze
from log  import logger
import threading
from collections import deque
import asyncio
import json
from proto import Proto
import websockets
from key import _WbiSigner
import weakref
import aiohttp
import yarl
import struct
import enum

HEADER_STRUCT = struct.Struct('>I2H2I')

class Command():
    def __init__(self, time, uid, text):
        self.uid = uid
        self.time = time
        self.text = text

class HeaderTuple(NamedTuple):
    pack_len: int
    raw_header_size: int
    ver: int
    operation: int
    seq_id: int

class Operation(enum.IntEnum):
    HEARTBEAT = 2
    AUTH = 7

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
        self._websocket = None
        self.webUrl = webUrl
        self._heartbeat_interval = 30  # default heartbeat interval in seconds
        self.heartbeat_timeout = 10  # default heartbeat timeout in seconds
        self.heartbeat_body = heartbeat_body
        self.token = ""
        self.hosts = []
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        self._wbi_signer = _get_wbi_signer(self._session)
        self._uid = None
        self._heartbeat_timer_handle: Optional[asyncio.TimerHandle] = None
    
    async def _init_uid(self):
        cookies = self._session.cookie_jar.filter_cookies(yarl.URL(UID_INIT_URL))
        sessdata_cookie = cookies.get('SESSDATA', None)
        if sessdata_cookie is None or sessdata_cookie.value == '':
            # cookie都没有，不用请求了
            self._uid = 0
            return True

        try:
            async with self._session.get(
                UID_INIT_URL,
                headers={'User-Agent': USER_AGENT},
            ) as res:
                if res.status != 200:
                    logger.warning('room=%d _init_uid() failed, status=%d, reason=%s', self._tmp_room_id,
                                   res.status, res.reason)
                    return False
                data = await res.json()
                if data['code'] != 0:
                    if data['code'] == -101:
                        # 未登录
                        self._uid = 0
                        return True
                    logger.warning('room=%d _init_uid() failed, message=%s', self._tmp_room_id,
                                   data['message'])
                    return False

                data = data['data']
                if not data['isLogin']:
                    # 未登录
                    self._uid = 0
                else:
                    self._uid = data['mid']
                return True
        except (aiohttp.ClientConnectionError, asyncio.TimeoutError):
            logger.exception('room=%d _init_uid() failed:', self._tmp_room_id)
            return False
    
    def _get_buvid(self):
        cookies = self._session.cookie_jar.filter_cookies(yarl.URL(BUVID_INIT_URL))
        buvid_cookie = cookies.get('buvid3', None)
        if buvid_cookie is None:
            return ''
        return buvid_cookie.value
    
    async def _init_buvid(self):
        try:
            async with self._session.get(
                BUVID_INIT_URL,
                headers={'User-Agent': USER_AGENT},
            ) as res:
                if res.status != 200:
                    logger.warning('room=%d _init_buvid() status error, status=%d, reason=%s',
                                   self._tmp_room_id, res.status, res.reason)
        except (aiohttp.ClientConnectionError, asyncio.TimeoutError):
            logger.exception('room=%d _init_buvid() exception:', self._tmp_room_id)
        return self._get_buvid() != ''
    
    @staticmethod
    def _make_packet(data: Union[dict, str, bytes], operation: int) -> bytes:
        """
        创建一个要发送给服务器的包

        :param data: 包体JSON数据
        :param operation: 操作码，见Operation
        :return: 整个包的数据
        """
        if isinstance(data, dict):
            body = json.dumps(data).encode('utf-8')
        elif isinstance(data, str):
            body = data.encode('utf-8')
        else:
            body = data
        header = HEADER_STRUCT.pack(*HeaderTuple(
            pack_len=HEADER_STRUCT.size + len(body),
            raw_header_size=HEADER_STRUCT.size,
            ver=1,
            operation=operation,
            seq_id=1
        ))
        return header + body

    
    async def fetch_room_id(self):
        if self._uid is None:
            if not await self._init_uid():
                logger.pr_error('room=%d _init_uid() failed', self.room_id)
                self._uid = 0

        if self._get_buvid() == '':
            if not await self._init_buvid():
                logger.pr_error('room=%d _init_buvid() failed', self.room_id)

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

    async def _send_heartbeat(self):
        """
        发送心跳包
        """
        print(f"Sending heartbeat to room {self.room_id}")
        if self._websocket is None or self._websocket.closed:
            return

        try:
            await self._websocket.send_bytes(self._make_packet({}, Operation.HEARTBEAT))
            print(f"Sent heartbeat to room {self.room_id}")
        except (ConnectionResetError, aiohttp.ClientConnectionError) as e:
            logger.pr_error('room=%d _send_heartbeat() failed: %r', self.room_id, e)
        except Exception:  # noqa
            logger.pr_error('room=%d _send_heartbeat() failed:', self.room_id)

    async def send_heartbeat(self):
        print(f"Scheduling next heartbeat for room {self.room_id}")
        if self._websocket is None or self._websocket.closed:
            print(f"WebSocket is closed, stopping heartbeat for room {self.room_id}")
            self._heartbeat_timer_handle = None
            return

        self._heartbeat_timer_handle = asyncio.get_running_loop().call_later(
            self._heartbeat_interval, self.send_heartbeat
        )
        asyncio.create_task(self._send_heartbeat())

    def _get_ws_url(self, retry_count) -> str:
        """
        返回WebSocket连接的URL，可以在这里做故障转移和负载均衡
        """
        host_server = self.hosts[retry_count % len(self.hosts)]
        return f"wss://{host_server['host']}:{host_server['wss_port']}/sub"

    async def _on_ws_close(self):
        """
        WebSocket连接断开
        """
        if self._heartbeat_timer_handle is not None:
            self._heartbeat_timer_handle.cancel()
            self._heartbeat_timer_handle = None

    async def on_connect(self):
        # 构建并发送 auth 包
        auth_packet = self._make_packet(
            data = {
                "uid": myUid,
                "roomid": self.room_id,
                "protover": 3,
                "buvid": self._get_buvid(),
                "support_ack": True,
                "queue_uuid": "dlg4u0p5",
                "scene": "room",
                "platform": "web",
                "type": 2,
                "key": self.token,
            }, operation=Operation.AUTH)
        print(f"Auth packet data: {auth_packet}")
        await self._websocket.send_bytes(auth_packet)
        print(f"Sent auth packet for room {self.room_id}")
        # 启动心跳任务
        self._heartbeat_timer_handle = asyncio.get_running_loop().call_later(
            self._heartbeat_interval, self.send_heartbeat
        )
    
    async def _on_ws_message(self, message: aiohttp.WSMessage):
        """
        收到WebSocket消息

        :param message: WebSocket消息
        """
        if message.type != aiohttp.WSMsgType.BINARY:
            logger.warning('room=%d unknown websocket message type=%s, data=%s', self.room_id,
                           message.type, message.data)
            return

        try:
            print(f"Received WebSocket message of length {len(message.data)}")
        except Exception:  # noqa
            logger.pr_error('room=%d _parse_ws_message() error:', self.room_id)

    async def connect_to_host(self):
        # 遍历返回的 host_list，逐个尝试连接并鉴权
        retry_count = 0
        total_retry_count = 0
        while True:
            try:
                async with self._session.ws_connect(
                    self._get_ws_url(retry_count),
                    headers={'User-Agent': USER_AGENT},
                    receive_timeout=self.heartbeat_timeout + 5,
                ) as websocket: 
                    self._websocket = websocket
                    logger.pr_info(f"Connected to WebSocket at {self._get_ws_url(retry_count)}")

                    await self.on_connect()
                    
                    message: aiohttp.WSMessage
                    async for message in websocket:
                        await self._on_ws_message(message)
                        # 至少成功处理1条消息
                        retry_count = 0

            except (aiohttp.ClientConnectionError, asyncio.TimeoutError):
                # 掉线重连
                print(f"room={self.room_id} connection lost, retrying...")
                pass
            finally:
                self._websocket = None
                await self._on_ws_close()
        
            retry_count += 1
            total_retry_count += 1
            logger.pr_info(f"room={self.room_id} is reconnecting, retry_count={retry_count}, total_retry_count={total_retry_count}")
            await asyncio.sleep(5)

    async def close(self):
        try:
            if self._session and not self._session.closed:
                await self._session.close()
                logger.pr_debug("Closed aiohttp session")
        except Exception as e:
            logger.pr_error(f"Error while closing session: {e}")

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
    try:
        await bsclient.connect_to_host()
    except Exception as e:
        logger.pr_error(f"Failed to connect to WebSocket: {e}")
    finally:
        await bsclient.close()
    
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
