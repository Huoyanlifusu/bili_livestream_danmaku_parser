import time
from collections import defaultdict
baseUrl = "https://api.live.bilibili.com/xlive/web-room/v1/dM/gethistory"
webUrl = "https://api.live.bilibili.com/xlive/web-room/v1/index/getDanmuInfo"
roomUrl = "https://api.live.bilibili.com/room/v1/Room/get_info"
websocketUrl = "wss://broadcast.chat.bilibili.com/sub"
headers = {
    'Host': 'api.live.bilibili.com',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36'
}

rmId = 22637261 # represent for bilibili stream room id
rmType = 0
params = {
    'id': rmId,
    "type": rmType
}
data = {
    'roomid': rmId,
    "room_type": rmType
}

usrCommLimit = 10

keyboardMap = {
    '111': 'e',
    '222': 'shift',
    '333': 'r',
    '444': 'q',
    '555': 'x',
    '666': 'c',
}

pressMap = {
    'e': 0,
    'shift': 0,
    'r': 0,
    'q': 0,
    'x': 0,
}

curyear = 2026
ytd, mtd = 12 * 31, 31
hts, mts = 3600, 60

sleepTime = 2
def sleep():
    time.sleep(sleepTime)