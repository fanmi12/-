# -*- coding: utf-8 -*-
"""
微信批量消息发送工具 - 入口文件
基于 wxauto 的微信自动化批量消息发送 GUI 工具
"""

import logging
import os
import traceback
from logging.handlers import RotatingFileHandler
from tkinter import messagebox

from core.app import WeChatBatchSender

# 配置日志
log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        RotatingFileHandler(
            os.path.join(log_dir, "wxauto_batch_sender.log"),
            encoding="utf-8",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
        ),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


def main():
    """主函数"""
    try:
        app = WeChatBatchSender()
        app.run()
    except Exception as e:
        logger.error(f"应用运行失败: {e}")
        logger.error(traceback.format_exc())
        messagebox.showerror("错误", f"应用运行失败: {e}")


if __name__ == "__main__":
    main()
