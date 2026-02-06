import logging

class Logger:
    def __init__(self):
        logging.basicConfig(
            level=logging.DEBUG, # 输出 DEBUG 及以上级别
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            filename="bili_capcmdtokb_info.log", # 输出到文件
            filemode="a", # 追加模式
            encoding="utf-8"
        )

    def pr_info(self, text):
        logging.info(text)
    def pr_debug(self, text):
        logging.debug(text)
    def pr_error(self, text):
        logging.error(text)

logger = Logger()