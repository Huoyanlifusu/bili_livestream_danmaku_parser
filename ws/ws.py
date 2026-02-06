from typing import Union
import aiohttp, asyncio, yarl, weakref, json, requests
from log.log import logger
from ws.key import _WbiSigner
from ws.proto import Proto
from ws.danmaku_parser import parse_ws_stream, extract_comment_info, Operation
from ws.util import HEADERS, USER_AGENT
#### web socket in debugging temporarily ####

WEB_URL = "https://api.live.bilibili.com/xlive/web-room/v1/index/getDanmuInfo"
UID_INIT_URL = "https://api.bilibili.com/x/web-interface/nav"
BUVID_INIT_URL = "https://www.bilibili.com/"
ROOM_URL = "https://api.live.bilibili.com/room/v1/Room/get_info"
rmId = 468200 # represent for bilibili stream room id
rmType = 0
params = {
    'id': rmId,
    "type": rmType
}


_session_to_wbi_signer = weakref.WeakKeyDictionary()
def _get_wbi_signer(session: aiohttp.ClientSession) -> '_WbiSigner':
    wbi_signer = _session_to_wbi_signer.get(session, None)
    if wbi_signer is None:
        wbi_signer = _session_to_wbi_signer[session] = _WbiSigner(session)
    return wbi_signer

class BiliStreamClient():
    def __init__(self, room_id=0):
        self.room_id = room_id
        self._websocket = None
        self._heartbeat_interval = 30  # default heartbeat interval in seconds
        self.receive_timeout = 1000  # default receive timeout in seconds
        self.token = ""
        self.hosts = []
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        self._wbi_signer = _get_wbi_signer(self._session)
        self._uid = None
    
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
    def make_packet(data: Union[dict, str, bytes], operation: int) -> bytes:
        proto = Proto()
        if isinstance(data, dict):
            proto.body = json.dumps(data)
        elif isinstance(data, str):
            proto.body = data
        else:
            proto.body = data.decode('utf-8')
        if operation not in (Operation.HEARTBEAT, Operation.AUTH):
            logger.pr_error(f"Unknown operation code: {operation}, defaulting to HEARTBEAT")
            return

        proto.op = operation
        return proto.pack()
    
    async def fetch_room_id(self):
        if self._uid is None:
            if not await self._init_uid():
                logger.pr_error('room=%d _init_uid() failed', self.room_id)
                self._uid = 0

        if self._get_buvid() == '':
            if not await self._init_buvid():
                logger.pr_error('room=%d _init_buvid() failed', self.room_id)

        try:
            html = requests.get(url=ROOM_URL, params=params, headers=HEADERS)
        except requests.RequestException as e:
            logger.pr_error(f"HTTP request exception towards {ROOM_URL}: {e}")
            return
        
        if html.status_code != 200:
            logger.pr_error(f"HTTP request failed towards {ROOM_URL} with status code {html.status_code}")
            return
        
        if html.json().get('data', -1) == {}:
            logger.pr_error(f"API returned empty data from {ROOM_URL}")
            return
        
        self.room_id = html.json().get('data', {}).get('room_id', -1)
        if self.room_id == -1:
            logger.pr_error(f"Failed to fetch room_id from {ROOM_URL}")
            return
        
        logger.pr_debug(f"successfully fetched room_id {self.room_id} from {ROOM_URL}")

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
            html = requests.get(url=WEB_URL, params=params, headers=HEADERS)
        except requests.RequestException as e:
            logger.pr_error(f"HTTP request exception towards {WEB_URL}: {e}")
            return

        if html.status_code != 200:
            logger.pr_error(f"HTTP request failed towards {WEB_URL} with status code {html.status_code}")
            return

        if html.json().get('code', -1) != 0:
            logger.pr_error(f"API returned error code {html.json().get('code', -1)} from {WEB_URL}")
            logger.pr_error(f"Possible reasons for err code -352: invalid parameters or access restrictions.")
            return

        logger.pr_debug(f"successfully accessed websocket info from {WEB_URL}")
        self.token = html.json().get('data', {}).get('token', '')
        self.hosts = html.json().get('data', {}).get('host_list', [])

    async def _send_heartbeat(self):
        """
        发送心跳包
        """
        if self._websocket is None or self._websocket.closed:
            return

        while True:
            try:
                await self._websocket.send_bytes(
                    self.make_packet(
                        '[object Object]',
                        Operation.HEARTBEAT))
                logger.pr_debug(f"Sent heartbeat to room {self.room_id}")
            except (ConnectionResetError, aiohttp.ClientConnectionError) as e:
                logger.pr_error(f'room={self.room_id} _send_heartbeat() failed: {e}')
            except Exception:  # noqa
                logger.pr_error(f'room={self.room_id} _send_heartbeat() failed:')
            
            await asyncio.sleep(self._heartbeat_interval)

    async def send_heartbeat(self):
        if self._websocket is None or self._websocket.closed:
            logger.pr_error(f"WebSocket is closed, stopping heartbeat for room {self.room_id}")
            return
        logger.pr_debug(f"Prepare to send heartbeat to room {self.room_id}")
        hb_task = asyncio.create_task(self._send_heartbeat())

        await hb_task

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
        return

    async def on_connect(self):
        # 构建并发送 auth 包
        auth_packet = self.make_packet(
            data = {
                "uid": self._uid, # uid 默认为0，否则发鉴权包失败
                "roomid": self.room_id,
                "protover": 3,
                "buvid": self._get_buvid(),
                "support_ack": True,
                "queue_uuid": "g0myt1hu",
                "scene": "room",
                "platform": "web",
                "type": 2,
                "key": self.token  # 使用从API获取的token
            }, operation=Operation.AUTH)
        await self._websocket.send_bytes(auth_packet)
        logger.pr_debug(f"Sent auth packet for room {self.room_id}")
        # 启动心跳任务
        asyncio.create_task(self.send_heartbeat())

    async def _parse_ws_message(self, data: bytes):
        messages, errors = await parse_ws_stream(data)
        
        # 如果有错误，记录但继续处理成功的消息
        if errors:
            for err in errors:
                logger.pr_debug(f"Parse error at offset {err['offset']}: {err['error']}")
        
        # 处理成功解析的消息
        for message in messages:
            if message and isinstance(message, dict):
                comment = await extract_comment_info(message)
                if comment:
                    logger.pr_info(f"评论: {comment['nickname']}: {comment['text']}")
                    # 可以在这里调用其他处理函数
                else:
                    # 不是评论消息，可能是其他类型（LIVE、SUPER_CHAT 等）
                    cmd = message.get('cmd', 'UNKNOWN')
                    logger.pr_debug(f"Received {cmd} message")
    
    async def _on_ws_message(self, message: aiohttp.WSMessage):
        """
        收到WebSocket消息

        :param message: WebSocket消息
        """
        if message.type != aiohttp.WSMsgType.BINARY:
            logger.pr_error('room=%d unknown websocket message type=%s, data=%s', self.room_id,
                           message.type, message.data)
            return

        try:
            await self._parse_ws_message(message.data)
        except Exception as e:
            logger.pr_error(f'room={self.room_id} _parse_ws_message() error: {e}')

    async def connect_to_host(self):
        # 遍历返回的 host_list，逐个尝试连接并鉴权
        retry_count = 0
        total_retry_count = 0
        while True:
            try:
                async with self._session.ws_connect(
                    self._get_ws_url(retry_count),
                    headers={'User-Agent': USER_AGENT},
                    receive_timeout=self.receive_timeout,
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
                logger.pr_error(f"room={self.room_id} connection lost, retrying...")
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