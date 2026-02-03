import time
from collections import defaultdict
baseUrl = "https://api.live.bilibili.com/xlive/web-room/v1/dM/gethistory"
headers = {
    'Host': 'api.live.bilibili.com',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36'
}
rmId = 468200
rmType = 0
data = {
    'roomid': rmId,
    "room_type": rmType
}
sleepTime = 2
usrCommLimit = 10
keyboardMap = {
    '111': 'e',
    '222': 'shift',
    '333': 'r',
    '444': 'q',
}

pressMap = {
    'e': 0,
    'shift': 0,
    'r': 0,
    'q': 0,
}

def sleep():
    time.sleep(sleepTime)