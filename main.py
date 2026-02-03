import requests
from util import baseUrl, headers, data, sleep
from node import add_comment, cmd_analyze, cmd_analyze_debug
from log  import logger
import threading
class Command():
    def __init__(self, time, uid, text):
        self.uid = uid
        self.time = time
        self.text = text

@staticmethod
def catch_live_comment_html(url, headers, data):
    html = requests.post(url=baseUrl, headers=headers, data=data)
    return html

@staticmethod
def receive_comment(json):
    for com_li in json['data']['room']:
        if not com_li:
            logger.pr_error("fetch null command")
            continue
        cmd = Command(com_li['timeline'], com_li['uid'], com_li['text'])  
        add_comment(cmd)

def catch_with_https():
    while True:
        html = catch_live_comment_html(baseUrl, headers, data)
        receive_comment(html.json())
        sleep()

def catch_with_https_debug():
    html = catch_live_comment_html(baseUrl, headers, data)
    receive_comment(html.json())
    sleep()

# while True:
if __name__ == "__main__":
    thread_c = threading.Thread(target=catch_with_https, daemon=True)
    thread_m = threading.Thread(target=cmd_analyze, daemon=True)

    thread_c.start()
    thread_m.start()

    thread_c.join()
    thread_m.join()

    # debug mode
    # catch_with_https_debug()
    # cmd_analyze_debug()

