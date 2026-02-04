import requests
from util import baseUrl, webUrl, roomUrl, headers, params, data, websocketUrl, params_dminfo
from util import sleep
from node import add_comment, cmd_analyze, cmd_analyze_debug
from log  import logger
import threading
from collections import deque
import asyncio
import zlib
import json
from aiowebsocket.converses import AioWebSocket
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

class BiliWebsocket():
    def __init__(self, roomid):
        self.hb='00 00 00 10 00 10 00 01  00 00 00 02 00 00 00 01'
        self.remote = 'wss://bd-sz-live-comet-05.chat.bilibili.com/sub'
        self.room_id = str(roomid)
        self.data_raw = '000000{headerLen}0010000100000007000000017b22726f6f6d6964223a{roomid}7d'
        self.data_raw = self.data_raw.format(
            headerLen = hex(27 + len(self.room_id))[2:],
            roomid = ''.join(map(lambda x: hex(ord(x))[2:], list(self.room_id)))
        )
    async def startup(self):
        async with AioWebSocket(self.remote) as aws:
            converse = aws.manipulator
            await converse.send(bytes.fromhex(self.data_raw))
            print(1)
            tasks = [asyncio.create_task(self.receDM(converse)), asyncio.create_task(self.sendHeartBeat(converse))]
            await asyncio.wait(tasks)

    async def sendHeartBeat(self, websocket):
        while True:
            await asyncio.sleep(30)
            await websocket.send(bytes.fromhex(self.hb))
            logger.pr_info('[Notice] Sent HeartBeat.')

    async def receDM(self, websocket):
        while True:
            print("Waiting for DM...")
            recv_text  = await websocket.receive()
            if recv_text == None:
                recv_text = b'\x00\x00\x00\x1a\x00\x10\x00\x01\x00\x00\x00\x08\x00\x00\x00\x01{"code":0}'
            self.printDM(recv_text)
    
    def printDM(self, data):
        # 获取数据包的长度，版本和操作类型
        packetLen = int(data[:4].hex(), 16)
        ver = int(data[6:8].hex(), 16)
        op = int(data[8:12].hex(), 16)

        # 有的时候可能会两个数据包连在一起发过来，所以利用前面的数据包长度判断，
        if (len(data) > packetLen):
            self.printDM(data[packetLen:])
            data = data[:packetLen]

        if (ver == 2):
            data = zlib.decompress(data[16:])
            self.printDM(data)
            return

        if (ver == 1):
            if (op == 3):
                logger.pr_info('[RENQI]  {}'.format(int(data[16:].hex(), 16)))
            return

        # ver 不为2也不为1目前就只能是0了，也就是普通的 json 数据。
        # op 为5意味着这是通知消息，cmd 基本就那几个了。
        if (op == 5):
            try:
                jd = json.loads(data[16:].decode('utf-8', errors='ignore'))
                if (jd['cmd'] == 'DANMU_MSG'):
                    logger.pr_info('[DANMU] {} : {}'.format(jd['info'][2][1], jd['info'][1]))
                elif (jd['cmd'] == 'SEND_GIFT'):
                    logger.pr_info('[GITT] {} {} {}x {}'.format(jd['data']['uname'], jd['data']['action'], jd['data']['num'], jd['data']['giftName']))
                elif (jd['cmd'] == 'LIVE'):
                    logger.pr_info('[Notice] LIVE Start!')
                elif (jd['cmd'] == 'PREPARING'):
                    logger.pr_info('[Notice] LIVE Ended!')
                else:
                    logger.pr_debug('[OTHER] {}'.format(jd['cmd']))
            except Exception as e:
                logger.pr_error(f"Error processing JSON data: {e}")

@staticmethod
def catch_live_comment_html(url, headers, data):
    html = requests.post(url=url, headers=headers, data=data)
    if html.status_code != 200:
        logger.pr_error(f"HTTP request failed towards {url} with status code {html.status_code}")
        return None

    return html

@staticmethod
async def catch_room_id(url, headers, params):
    try:
        html = requests.get(url=url, params=params, headers=headers)
    except requests.RequestException as e:
        logger.pr_error(f"HTTP request exception towards {url}: {e}")
        return -1
    if html.status_code != 200:
        logger.pr_error(f"HTTP request failed towards {url} with status code {html.status_code}")
        return -1
    logger.pr_info(f"successfully fetched room_id {html.json().get('data', {})} from {url}")
    return html.json().get('data', {}).get('room_id', -1)

@staticmethod
async def access_bili_websocket_html(url, headers, params):
    try:
        html = requests.get(url=url, params=params, headers=headers)
    except requests.RequestException as e:
        logger.pr_error(f"HTTP request exception towards {url}: {e}")
        return None

    if html.status_code != 200:
        logger.pr_error(f"HTTP request failed towards {url} with status code {html.status_code}")
        return None

    if html.json().get('code', -1) != 0:
        logger.pr_error(f"API returned error code {html.json().get('code', -1)} from {url}")
        logger.pr_error(f"Possible reasons for err code -352: invalid parameters or access restrictions.")
        return None

    logger.pr_info(f"successfully accessed websocket info from {url}")
    return html.json()

def catch_with_https():
    while True:
        catch_with_https_debug()

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
        roomid = asyncio.run(catch_room_id(roomUrl, headers, params))
        if roomid == -1:
            logger.pr_error("Failed to fetch room ID")
            return
        
        json = asyncio.run(access_bili_websocket_html(webUrl, headers, params_dminfo))
        if not json:
            logger.pr_error("Failed to access BiliBili WebSocket info")
            return
        token = json.get('data', {}).get('token', '')
        hosts = json.get('data', {}).get('host_list', [])
        if not token or not hosts:
            logger.pr_error("Token or host list is missing in WebSocket info")
            return
        logger.pr_debug(f"Fetched token: {token}\n \t\t\t hosts: {hosts}")
        # loop = asyncio.new_event_loop()
        # asyncio.set_event_loop(loop)
        # try:
        #     loop.run_until_complete(BiliWebsocket(roomid).startup())
        # finally:
        #     loop.close()
    except Exception as e:
        logger.pr_error(f"Error in debug mode: {e}")

if __name__ == "__main__":
    # thread_c = threading.Thread(target=catch_with_https, daemon=True)
    # thread_m = threading.Thread(target=cmd_analyze, daemon=True)

    # thread_c.start()
    # thread_m.start()

    # thread_c.join()
    # thread_m.join()

    # Dbg mode
    debug_mode()

