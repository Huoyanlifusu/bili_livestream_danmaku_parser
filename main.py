import requests
from util import baseUrl, webUrl, roomUrl, headers, params, data
from util import sleep
from node import add_comment, cmd_analyze, cmd_analyze_debug
from log  import logger
import threading
from collections import deque
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

def catch_room_id(url, headers, params):
    try:
        html = requests.get(url=url, params=params, headers=headers)
    except requests.RequestException as e:
        logger.pr_error(f"HTTP request exception towards {url}: {e}")
        return -1
    if html.status_code != 200:
        logger.pr_error(f"HTTP request failed towards {url} with status code {html.status_code}")
        return -1
    logger.pr_info("successfully fetched room_id from {url}")
    return html.json().get('data', {}).get('room_id', -1)


@staticmethod
def access_bili_websocket_html(url, headers, params):
    try:
        html = requests.get(url=url, params=params, headers=headers)
    except requests.RequestException as e:
        logger.pr_error(f"HTTP request exception towards {url}: {e}")
        return None

    if html.status_code != 200:
        logger.pr_error(f"HTTP request failed towards {url} with status code {html.status_code}")
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
    rmid = catch_room_id(roomUrl, headers, params)
    if rmid == -1:
        logger.pr_error("Failed to fetch room id")
        exit(-1)
    logger.pr_info(f"Fetched room id: {rmid}")
    html = access_bili_websocket_html(webUrl, headers, params)
    logger.pr_info(html)
    # catch_with_https_debug()
    # cmd_analyze_debug()

if __name__ == "__main__":
    # thread_c = threading.Thread(target=catch_with_https, daemon=True)
    # thread_m = threading.Thread(target=cmd_analyze, daemon=True)

    # thread_c.start()
    # thread_m.start()

    # thread_c.join()
    # thread_m.join()

    # Dbg mode
    debug_mode()

