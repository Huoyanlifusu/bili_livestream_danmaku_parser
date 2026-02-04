import time

baseUrl = "https://api.live.bilibili.com/xlive/web-room/v1/dM/gethistory"
webUrl = "https://api.live.bilibili.com/xlive/web-room/v1/index/getDanmuInfo"
roomUrl = "https://api.live.bilibili.com/room/v1/Room/get_info"
headers = {
    'Host': 'api.live.bilibili.com',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36'
}
heartbeat_body = "5b6f626a656374204f626a6563745d"
myUid = 3056211 # replace with your own Bilibili UID
rmId = 468200 # represent for bilibili stream room id
rmType = 0
params = {
    'id': rmId,
    "type": rmType
}
params_dminfo = {
    "id": "468200",          # 房间ID
    "type": "0",             # 类型标识
    "web_location": "444.8", # 网页位置参数
    "w_rid": "4bb45fba315da3d4a23ddb425694985b",  # 签名参数
    "wts": "1770176148"      # 时间戳参数
}
# params_dminfo = {
#     'room_id': rmId,
#     'type': rmType,
#     'web_location': '444.8',
#     'w_rid': '4bb45fba315da3d4a23ddb425694985b',
#     'wts': '1770176148'
# }
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