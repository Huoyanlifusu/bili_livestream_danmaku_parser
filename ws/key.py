# copied from https://github.com/xfgryujk/blivedm/blob/dev/blivedm/clients/

import datetime
import hashlib
import urllib.parse
import aiohttp
from typing import Optional, Awaitable
import asyncio
from ws.util import USER_AGENT
from log.log import logger

WBI_INIT_URL = 'https://api.bilibili.com/x/web-interface/nav'

class _WbiSigner:
    WBI_KEY_INDEX_TABLE = [
        46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
        27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13
    ]
    """wbi密码表"""
    WBI_KEY_TTL = datetime.timedelta(hours=11, minutes=59, seconds=30)

    def __init__(self, session: aiohttp.ClientSession):
        self._session = session

        self._wbi_key = ''
        """缓存的wbi鉴权口令"""
        self._refresh_future: Optional[Awaitable] = None
        """用来避免同时刷新"""
        self._last_refresh_time: Optional[datetime.datetime] = None

    @property
    def wbi_key(self) -> str:
        """
        缓存的wbi鉴权口令
        """
        return self._wbi_key

    def reset(self):
        self._wbi_key = ''
        self._last_refresh_time = None

    @property
    def need_refresh_wbi_key(self):
        return self._wbi_key == '' or (
            self._last_refresh_time is not None
            and datetime.datetime.now() - self._last_refresh_time >= self.WBI_KEY_TTL
        )

    def refresh_wbi_key(self) -> Awaitable:
        if self._refresh_future is None:
            self._refresh_future = asyncio.create_task(self._do_refresh_wbi_key())

            def on_done(_fu):
                self._refresh_future = None
            self._refresh_future.add_done_callback(on_done)

        return self._refresh_future

    async def _do_refresh_wbi_key(self):
        wbi_key = await self._get_wbi_key()
        if wbi_key == '':
            return

        self._wbi_key = wbi_key
        self._last_refresh_time = datetime.datetime.now()

    async def _get_wbi_key(self):
        try:
            async with self._session.get(
                WBI_INIT_URL,
                headers={'User-Agent': USER_AGENT},
            ) as res:
                if res.status != 200:
                    logger.pr_error('WbiSigner failed to get wbi key: status=%d %s', res.status, res.reason)
                    return ''
                data = await res.json()
        except (aiohttp.ClientConnectionError, asyncio.TimeoutError):
            logger.pr_error('WbiSigner failed to get wbi key:')
            return ''

        try:
            wbi_img = data['data']['wbi_img']
            img_key = wbi_img['img_url'].rpartition('/')[2].partition('.')[0]
            sub_key = wbi_img['sub_url'].rpartition('/')[2].partition('.')[0]
        except KeyError:
            logger.pr_error('WbiSigner failed to get wbi key: data=%s', data)
            return ''

        shuffled_key = img_key + sub_key
        wbi_key = []
        for index in self.WBI_KEY_INDEX_TABLE:
            if index < len(shuffled_key):
                wbi_key.append(shuffled_key[index])
        return ''.join(wbi_key)

    def add_wbi_sign(self, params: dict):
        if self._wbi_key == '':
            return params

        wts = str(int(datetime.datetime.now().timestamp()))
        params_to_sign = {**params, 'wts': wts}

        # 按key字典序排序
        params_to_sign = {
            key: params_to_sign[key]
            for key in sorted(params_to_sign.keys())
        }
        # 过滤一些字符
        for key, value in params_to_sign.items():
            value = ''.join(
                ch
                for ch in str(value)
                if ch not in "!'()*"
            )
            params_to_sign[key] = value

        str_to_sign = urllib.parse.urlencode(params_to_sign) + self._wbi_key
        w_rid = hashlib.md5(str_to_sign.encode('utf-8')).hexdigest()
        return {
            **params,
            'wts': wts,
            'w_rid': w_rid
        }