from util import usrCommLimit, keyboardMap, sleep, pressMap
from util import ytd, mtd, hts, mts, curyear
from heapq import heappush, heappop
import re
from collections import defaultdict
from log import logger
import keyboard

def singleton(cls):
    instances = {}
    def get_instance(*args, **kwargs):
        if cls not in instances:
            instances[cls] = cls(*args, **kwargs)

        return instances[cls]

    return get_instance

@singleton
class keyboardToucher():
    def receive_command(self, button):
        if button not in pressMap:
            logger.pr_error(f"Received undefined button type {button}")

        if not pressMap[button]:
            logger.pr_info(f"Pressing button {button}")
            keyboard.press_and_release(button)
            return

    def monitor(self):
        while not g_cmdlist.isEmpty():
            _, cmdtext, uid = g_cmdlist.pop()
            userNode.remove_comment(uid)
            self.receive_command(keyboardMap[cmdtext])
        

@singleton
class commandList():
    def __init__(self):
        self.command_list = []

    def isEmpty(self) -> bool:
        return self.command_list == []

    def push(self, item):
        heappush(self.command_list, item)

    def pop(self) -> tuple:
        if self.command_list == []:
            return None

        return heappop(self.command_list)


class userNode():
    g_node_ht = defaultdict(dict)
    g_last_time = 0

    @classmethod
    def create_node(cls, uid):
        cls.g_node_ht[uid]['textnum'] = 0

    @classmethod
    def check_limit(cls, uid) -> bool:
        if uid not in cls.g_node_ht:
            cls.create_node(uid)

        if cls.g_node_ht[uid]['textnum'] >= usrCommLimit:
            return False

        return True

    @classmethod
    def add_comment(cls, uid):
        cls.g_node_ht[uid]['textnum'] += 1

    @classmethod
    def remove_comment(cls, uid):
        cls.g_node_ht[uid]['textnum'] -= 1

    @classmethod
    def filter(cls, text) -> str:
        f_text = re.sub("[a-zA-Z]", "", text)
        if f_text not in keyboardMap:
            return ""

        return f_text

g_kbtoucer = keyboardToucher()
g_cmdlist = commandList()

@staticmethod
def process_time(time):
    ymd = [int(x) for x in time.split(" ")[0].split("-")]
    hms = [int(x) for x in time.split(" ")[1].split(":")]
    if len(ymd) != 3 or len(hms) != 3:
        return -1

    ymd = (ymd[0] - curyear) * ytd + ymd[1] * mtd + ymd[2]
    hms = hms[0] * hts + hms[1] * mts + hms[2]

    return (ymd << 17) | (hms & 0x1ffff)

def add_comment(command):
    uid = command.uid
    text = command.text
    time = command.time

    if not userNode.check_limit(uid):
        logger.pr_debug(f"uid {uid} has reached limits")
        return

    f_text = userNode.filter(text)
    if f_text == "":
        logger.pr_debug(f"uid {uid} has filtered out text {text}")
        return

    processed_time = process_time(time)
    if processed_time == -1:
        logger.pr_error(f"Invalid time format: {time}")
        return

    logger.pr_info(f"Accepted command {f_text} from uid {uid} at time {time}")
    userNode.add_comment(uid)
    g_cmdlist.push((processed_time, f_text, uid))

def cmd_analyze():
    while True:
        cmd_analyze_debug()

def cmd_analyze_debug():
    g_kbtoucer.monitor()
    sleep()