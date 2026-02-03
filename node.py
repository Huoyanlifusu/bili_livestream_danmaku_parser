from util import usrCommLimit, keyboardMap, sleep, pressMap
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
            keyboard.press_and_release(button)
            return
    def monitor(self):
        while not g_cmdlist.isEmpty:
            _, cmdtext = g_cmdlist.pop()
            self.receive_command(keyboardMap[cmdtext])
        

@singleton
class commandList():
    def __init__(self):
        self.command_list = []

    def isEmpty(self):
        return self.command_list == []

    def push(self, item):
        heappush(self.command_list, item)

    def pop(self):
        if self.command_list == []:
            return None
        return heappop(self.command_list)


class userNode():
    g_node_ht = defaultdict(int)
    def __init__(self, uid):
        self.uid = uid

    @classmethod
    def create_node(cls, uid):
        cls.g_node_ht[uid] = 0

    @classmethod
    def check_limit(cls, uid):
        if uid not in cls.g_node_ht:
            cls.create_node(uid)
        if cls.g_node_ht[uid] >= usrCommLimit:
            return False
        cls.g_node_ht[uid] += 1
        return True

    def filter(self, text):
        f_text = re.sub("[a-zA-Z]", "", text)
        if f_text in keyboardMap:
            print(f_text)
            return True
        return False

g_kbtoucer = keyboardToucher()
g_cmdlist = commandList()

def cmd_analyze():
    while True:
        g_kbtoucer.monitor()
        sleep()

@staticmethod
def process_time(time):
    return int("".join(time.split(" ")[0].split("-"))) << 20 + int("".join(time.split(" ")[1].split(":"))) & 0xfffff

def add_comment(command):
    uid = command.uid
    text = command.text
    time = command.time
    if not userNode.check_limit(uid):
        logger.pr_debug(f"uid {uid} has reached limits")
    node = userNode(uid)
    if node.filter(text):
        processed_time = process_time(time)
        g_cmdlist.push((processed_time, text))