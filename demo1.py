from log.log import logger
import asyncio
from ws.ws import BiliStreamClient

### 弹幕互动，客户端发送弹幕反馈至主机硬件设备

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
    
    # 链接服务器，web socket协议
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
    # websocket protocol
    debug_mode()
