"""
Bilibili WebSocket Danmaku (弹幕) Parser
解析 WebSocket 二进制弹幕数据包，提取评论信息
"""
import struct
import json
import brotli
import zlib
from typing import NamedTuple, Tuple, Optional, Dict, Any
from enum import IntEnum
from log.log import logger
# Header 定义
HEADER_STRUCT = struct.Struct('>I2H2I')

class HeaderTuple(NamedTuple):
    pack_len: int          # 包总长度
    raw_header_size: int   # header 长度（通常 16）
    ver: int               # 协议版本
    operation: int         # 操作码
    seq_id: int            # 序列号

class ProtoVer(IntEnum):
    """协议版本"""
    NORMAL = 0
    HEARTBEAT = 1
    DEFLATE = 2
    BROTLI = 3

class Operation(IntEnum):
    """操作码"""
    HEARTBEAT = 2
    HEARTBEAT_REPLY = 3
    SEND_MSG = 4
    SEND_MSG_REPLY = 5
    AUTH = 7
    AUTH_REPLY = 8

hb_recv_format = {"recv_msg":"heartbeat_reply", "operation":Operation.HEARTBEAT_REPLY}

async def parse_header(data: bytes, offset: int = 0) -> Tuple[HeaderTuple, Optional[Exception]]:
    """
    从二进制数据中解析 header
    返回 (HeaderTuple, error)；如果解析失败 error 不为 None
    """
    try:
        if len(data) - offset < HEADER_STRUCT.size:
            return None, ValueError(f"Data too short: {len(data) - offset} < {HEADER_STRUCT.size}")
    
        return HeaderTuple(*HEADER_STRUCT.unpack_from(data, offset)), None
    except struct.error as e:
        return None, e

def extract_body(packet: bytes, header: HeaderTuple, offset: int = 0) -> bytes:
    """
    从包中提取 body（根据 header.raw_header_size 和 header.pack_len）
    """
    start = offset + header.raw_header_size
    end = offset + header.pack_len
    return packet[start:min(end, len(packet))]

async def decompress_body(body: bytes, ver: ProtoVer) -> Tuple[Optional[bytes], Optional[Exception]]:
    """
    根据协议版本解压 body
    返回 (decompressed_body, error)
    
    注意：
    - ProtoVer.NORMAL (0) = 未压缩 JSON
    - ProtoVer.HEARTBEAT (1) = 心跳应答（仅包含 4 字节的人气值）
    - ProtoVer.DEFLATE (2) = zlib 压缩
    - ProtoVer.BROTLI (3) = brotli 压缩
    """
    try:
        if ver == ProtoVer.BROTLI:
            return brotli.decompress(body), None
        elif ver == ProtoVer.DEFLATE:
            return zlib.decompress(body), None
        elif ver == ProtoVer.NORMAL or ver == ProtoVer.HEARTBEAT:
            # HEARTBEAT 版本的 body 是原始二进制，不是 JSON
            return body, None
        else:
            return None, ValueError(f"Unknown protocol version: {ver}")
    except Exception as e:
        return None, e

def parse_danmaku_message(body: bytes, operation: int) -> Tuple[Optional[Dict[str, Any]], Optional[Exception]]:
    """
    解析业务消息，根据 operation 决定如何处理
    返回 (parsed_json, error)
    
    - SEND_MSG_REPLY (5) 和 AUTH_REPLY (8) = JSON 消息
    - HEARTBEAT_REPLY (3) = 前 4 字节为人气值，无 JSON
    """
    try:
        # 对于心跳应答，只取前 4 字节作为人气值
        if operation == Operation.HEARTBEAT_REPLY:
            if len(body) >= 4:
                popularity = int.from_bytes(body[:4], 'big')
                logger.pr_info(f"popularity {popularity}")
                return {'cmd': '_HEARTBEAT', 'popularity': popularity}, None
            else:
                return {'cmd': '_HEARTBEAT', 'popularity': 0}, None
        
        # 其他消息假设是 JSON
        text = json.loads(body.decode('utf-8'))
        return text, None
    except Exception as e:
        return None, e

async def extract_notice_info(message: Dict[str, Any]):
        data = message.get('data', '')
        if data and isinstance(data, dict) and data.get('notice_msg', ''):
            logger.pr_info(f"recv bilibili notice {data['notice_msg']}")
        if message.get('half', '') != '':
            pass

async def extract_comment_info(message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    从 DANMU_MSG 消息中提取评论关键信息
    返回 {"uid": int, "nickname": str, "text": str, "timestamp": int} 或 None
    """
    if not isinstance(message, dict):
        return None

    cmd = message.get('cmd', '')
    if cmd == 'LOG_IN_NOTICE':
        await extract_notice_info(message)
        return None
    elif cmd == 'STOP_LIVE_ROOM_LIST':
        return None
    elif cmd != 'DANMU_MSG':
        # 可能是其他消息类型（LIVE、SUPER_CHAT 等）
        return None

    try:
        info = message.get('info', [])
        if len(info) < 3:
            return None

        # B站弹幕结构：
        # info[0] = [评论详情数组] - 包含文本、时间戳等
        # info[1] = "1" (字符串) - 未使用
        # info[2] = [uid, nickname, ...] - 用户信息
        comment_detail = info[0]  # 第一个数组：评论详情
        if len(comment_detail) < 15:
            return None
        extra = comment_detail[15]
        if not isinstance(extra, dict):
            return None
        comment = json.loads(extra.get('extra', ''))
        if not comment["content"]:
            return None

        user_info_full = info[2]  # 用户信息数组

        timestamp_ms = comment_detail[4] if len(comment_detail) > 4 else 0

        uid = user_info_full[0] if len(user_info_full) > 0 else 0
        nickname = user_info_full[1] if len(user_info_full) > 1 else 'Unknown'

        return {
            'uid': uid,
            'nickname': nickname,
            'text': comment["content"],
            'timestamp': timestamp_ms,  # 毫秒级时间戳
        }
    except (IndexError, KeyError, TypeError) as e:
        return None

async def parse_ws_packet(packet: bytes, offset: int = 0) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    完整解析 WebSocket 包：header -> body -> 根据 ver 解压 -> JSON 解析
    返回 (parsed_message, error_message)
    """
    # 1. 解析 header
    header, header_err = await parse_header(packet, offset)
    if header_err:
        return None, f"Failed to parse header: {header_err}"

    if header.operation == Operation.AUTH_REPLY:
        logger.pr_info(f"received auth ack")
        return None, None
    
    if header.operation not in (Operation.SEND_MSG_REPLY, Operation.HEARTBEAT_REPLY):
        return None, f"received unknown operation {header.operation} msg"

    # 2. 提取 body（压缩/未压缩）
    body = extract_body(packet, header, offset)

    # 3. 根据 header.ver 决定是否解压
    decompressed_body, decomp_err = await decompress_body(body, ProtoVer(header.ver))
    if decomp_err:
        return None, f"Failed to decompress body (ver={header.ver}): {decomp_err}"
    if header.ver == 3:
        decompressed_body = decompressed_body[header.raw_header_size:]

    # 4. 根据 operation 解析消息
    message, parse_err = parse_danmaku_message(decompressed_body, header.operation)
    if parse_err:
        # 如果 JSON 解析失败，日志记录但返回错误
        return None, f"Failed to parse message (op={header.operation}): {parse_err}"
    
    if header.operation == Operation.HEARTBEAT_REPLY:
        logger.pr_info(f"received heartbeat ack")
        return hb_recv_format, None
    
    return message, None

async def parse_ws_stream(data: bytes) -> Tuple[list, list]:
    """
    解析包含多个包的 WebSocket 数据流
    返回 (messages: list, errors: list)
    """
    messages = []
    errors = []
    offset = 0

    while offset < len(data):
        if 0 < len(data) - offset < HEADER_STRUCT.size:
            errors.append({
                'offset': offset,
                'error': f'Incomplete header: only {len(data) - offset} bytes left'
            })
            break

        message, error = await parse_ws_packet(data, offset)
        if message == hb_recv_format:
            break
        if error:
            # 出错了，但继续尝试解析下一个包
            errors.append({'offset': offset, 'error': error})
        if message:
            messages.append(message)

        # 尝试解析 header 以获知包长度，移到下一个包
        header, header_err = await parse_header(data, offset)
        if header_err:
            break
        
        offset += header.pack_len
        if offset > len(data):
            break

    return messages, errors