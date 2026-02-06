from collections import deque
import re
from log.log import logger
import keyboard
import asyncio

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

# Async queue used by producer(s) and the async consumer.
# A small fallback buffer holds items pushed before the event loop starts.
command_queue: asyncio.Queue = asyncio.Queue()
_fallback_buffer: deque = deque()

def push_next_command(command) -> None:
    """Push a command. If an asyncio event loop is running, notify the async
    consumer by putting into `command_queue` using `call_soon_threadsafe`.
    If no loop is running yet, buffer into `_fallback_buffer` so the
    consumer will drain it when it starts.
    """
    logger.pr_debug(f"append button {command}")
    mapped = keyboardMap.get(command, "")
    if not mapped:
        logger.pr_debug(f"Unknown command key: {command}")
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop -> buffer the command for later
        _fallback_buffer.append(mapped)
    else:
        try:
            # Use call_soon_threadsafe so this function is safe to call
            # from other threads or synchronous contexts.
            loop.call_soon_threadsafe(command_queue.put_nowait, mapped)
        except Exception as e:
            logger.pr_error(f"Failed to push to queue: {e}")
            _fallback_buffer.append(mapped)

def filter(text) -> bool:
    f_text = re.sub("[a-zA-Z]", "", text)
    if f_text not in keyboardMap:
        return False
    return True

class keyboardToucher():
    def press(self, button):
        logger.pr_debug(f"Pressing button {button}")
        keyboard.press_and_release(button)

KBtoucher = keyboardToucher()

async def fetch_command_list():
    """Async consumer: drains any buffered commands first, then waits on the
    queue. This avoids busy-waiting and ensures push_next_command notifies
    the consumer immediately when the loop is running.
    """
    # Drain any commands pushed before the event loop started
    while _fallback_buffer:
        await command_queue.put(_fallback_buffer.popleft())

    while True:
        try:
            cmd = await command_queue.get()
            if cmd in pressMap:
                KBtoucher.press(cmd)
        except Exception as e:
            logger.pr_error(f"Error happen, {e}")
            # avoid tight error loop
            await asyncio.sleep(0.1)