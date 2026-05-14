# -*- coding: utf-8 -*-
"""WeChatBatchSender 主类"""

import ctypes
import json
import logging
import os
import random
import re
import threading
import time
import tkinter as tk
import traceback
import urllib.error
import urllib.request
from datetime import datetime
from tkinter import filedialog, messagebox, scrolledtext, ttk

from core.base import ScrollingStatus, BaseWeChatTool

try:
    from wxauto import WeChat
except ImportError:
    WeChat = None

import comtypes

logger = logging.getLogger(__name__)

_wechat_instance = None
_wechat_lock = threading.Lock()


def _check_wechat_window():
    """检查微信窗口是否存在且可见，返回 (是否就绪, 提示信息)"""
    try:
        import win32gui

        hwnd = win32gui.FindWindow("WeChatMainWndForPC", None)
        if not hwnd:
            return False, "未找到微信窗口，请先启动微信并登录"

        if not win32gui.IsWindowVisible(hwnd):
            return False, "微信窗口已最小化到托盘，请先点击托盘图标恢复微信窗口"

        title = win32gui.GetWindowText(hwnd)
        return True, f"微信窗口已找到（标题: {title or '微信'}）"
    except Exception:
        return True, ""


def get_wechat():
    """获取全局唯一的 WeChat 实例（线程安全，自动处理 COM 初始化）"""
    global _wechat_instance

    with _wechat_lock:
        if _wechat_instance is not None:
            try:
                if _wechat_instance.IsOnline():
                    return _wechat_instance
            except Exception:
                pass
            _wechat_instance = None

        if not WeChat:
            raise ImportError("未安装wxauto库")

        ready, msg = _check_wechat_window()
        if not ready:
            raise RuntimeError(msg)

        comtypes.CoInitialize()
        _wechat_instance = WeChat()
        return _wechat_instance


class WeChatBatchSender(BaseWeChatTool):
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("微信批量消息发送工具")
        self.root.geometry("700x700")  # 进一步压缩宽度，只占屏幕一半
        self.root.resizable(True, True)

        # ========== 核心修改：恢复默认图标（去掉小猪图标逻辑） ==========
        # 直接注释掉小猪图标相关代码，使用tk默认图标

        # 核心状态变量
        self.wx = None
        self.is_initialized = False
        self.is_sending = False
        self.is_paused = False
        self.is_refreshing = False
        self.limit_override_risk_boost = False
        self.current_contact_index = 0

        # Shift选择相关
        self.last_click_index = -1
        self.shift_selection_active = False

        # 数据存储
        self.contacts = []
        self.filtered_contacts = []
        self.selected_contacts = []
        self.image_paths = []  # 支持多张图片
        self.last_image_dir = None  # 记录上次打开图片的目录

        # 线程安全锁
        self._data_lock = threading.Lock()

        # 上次会话配置
        self.last_config_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "config", "last_session.json"
        )
        self.last_session_data = self.load_last_session_config()
        if self.last_session_data and "last_image_dir" in self.last_session_data:
            self.last_image_dir = self.last_session_data["last_image_dir"]

        # 发送结果统计
        self.send_results = {"success": [], "failed": [], "deleted": []}

        # 字体缩放配置
        self.base_font_size = 13
        self.current_font_size = 13
        self.min_font_size = 10
        self.max_font_size = 20
        self.initial_window_width = 700
        self.font_scale_factor = 1.0
        self.all_font_widgets = []  # 存储所有需要缩放的字体组件

        # 配置项（集中管理）
        self.config = {
            "send_from_selected": tk.BooleanVar(value=False),
            "skip_filtered_contacts": tk.BooleanVar(value=True),  # 不发送被筛选的联系人
            "send_order": tk.StringVar(
                value="images_first"
            ),  # 发送顺序：images_first（先发图片）或 text_first（先发短信）
            # ===== 防风控安全设置 =====
            "anti_risk_mode": tk.BooleanVar(
                value=False
            ),  # 默认关闭，开启后启用强力防封
            "daily_limit": tk.IntVar(value=1000),  # 每日发送上限
            "daily_sent_count": 0,
            "daily_sent_date": "",
            "kimi_rewrite_enabled": tk.BooleanVar(value=False),
            "kimi_api_key": tk.StringVar(value=""),
            "kimi_base_url": tk.StringVar(
                value="https://api.moonshot.cn/v1/chat/completions"
            ),
            "kimi_model": tk.StringVar(value="kimi-k2-turbo-preview"),
            "kimi_batch_size": tk.IntVar(value=20),
            "kimi_system_prompt": tk.StringVar(
                value="你是中文文案改写助手。保持原意，口吻亲和自然，避免生硬销售腔与夸张营销词。占位符必须与原模板完全一致，只保留原模板已有占位符，禁止新增或删除。输出纯文本，不要解释。"
            ),
            "message_template": "{name}，你好！\n这是一条测试消息。{emoji}",
            "delays": {
                "min": 0.8,
                "max": 1.5,
                "search_min": 0.2,
                "search_max": 0.5,
                "image_to_msg": 0.3,
            },
            "emojis": [
                "😊",
                "😀",
                "😄",
                "😁",
                "😆",
                "😇",
                "😉",
                "😍",
                "😘",
                "😗",
                "😙",
                "😚",
                "😋",
            ],
            "filter_keywords": [],
            # ===== 新增：统一配置管理 =====
            "hard_block_keywords": [],  # 硬黑名单：包含则不显示（预留）
            "clean_keywords": [],  # 内容清洗：发送但去除这些词
            "title_keywords": [],
            "grade_keywords": [],
            "noise_keywords": [],
            "compound_surnames": [],
            "delete_keywords": [
                "需要验证",
                "不是好友",
                "无法找到",
                "请先发送朋友验证申请",
                "好友关系验证",
                "开启了朋友验证",
                "开启了好友验证",
                "你还不是他（她）朋友",
                "需要发送朋友验证",
                "朋友验证请求",
                "发送验证申请",
                "验证申请",
                "验证信息",
                "朋友验证",
                "好友验证",
                "朋友验证已过期",
                "发送验证信息",
                "等待验证",
                "好友验证",
                "验证通过",
                "添加好友",
                "发送好友验证",
                "开启朋友验证",
                "对方开启了朋友验证",
                "请发送朋友验证",
                "您还不是对方的好友",
                "您不是对方的好友",
                "不是对方的好友",
                "消息已发出但被对方拒收",
                "消息被拒收",
                "拒收消息",
                "对方拒收了您的消息",
                "被拒收",
                "拒收",
            ],
            "invalid_keywords": [],
            "filter_chars": [],
            "filter_patterns": [],
            "exact_match_invalid_names": [],
        }
        self.kimi_current_template = None
        self.kimi_last_batch_index = -1

        # 加载应用配置 (提前加载，确保UI初始化时能读取到保存的配置)
        self.load_app_config()

        # 初始化界面
        self.setup_scrollable_ui()

        # 绑定事件
        self.bind_events()

        # 初始化微信（后台线程）
        threading.Thread(target=self.init_wechat, daemon=True).start()

    def _append_send_result(self, category, contact_info):
        """线程安全地添加发送结果"""
        with self._data_lock:
            self.send_results[category].append(contact_info)

    def _update_contacts(self, contacts, filtered_contacts=None):
        """线程安全地更新联系人列表"""
        with self._data_lock:
            self.contacts = contacts
            self.filtered_contacts = filtered_contacts if filtered_contacts is not None else contacts.copy()

    def load_last_session_config(self):
        """加载上次会话配置"""
        try:
            if os.path.exists(self.last_config_file):
                with open(self.last_config_file, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"加载上次配置失败: {e}")
        return {}

    def save_last_session_config(self):
        """保存当前会话配置"""
        try:
            config = {
                "last_image_dir": self.last_image_dir,
                "last_image_paths": self.image_paths,
            }
            with open(self.last_config_file, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=4)
        except Exception as e:
            logger.error(f"保存配置失败: {e}")

    def use_last_images(self):
        """使用上次选择的图片"""
        if (
            not self.last_session_data
            or "last_image_paths" not in self.last_session_data
        ):
            messagebox.showinfo("提示", "没有找到上次使用的图片记录")
            return

        last_paths = self.last_session_data["last_image_paths"]
        if not last_paths:
            messagebox.showinfo("提示", "上次使用的图片记录为空")
            return

        # 验证文件是否存在
        valid_paths = [p for p in last_paths if os.path.exists(p)]
        if not valid_paths:
            messagebox.showwarning("警告", "上次使用的图片文件已不存在！")
            return

        if len(valid_paths) < len(last_paths):
            self.update_log(
                f"⚠️ 部分上次使用的图片已丢失，仅加载存在的 {len(valid_paths)} 张"
            )

        self.image_paths = valid_paths

        # 更新输入框显示
        display_text = f"已选择 {len(self.image_paths)} 张图片：{os.path.basename(self.image_paths[0])}"
        if len(self.image_paths) > 1:
            display_text += " 等"
        self.image_path_entry.delete(0, tk.END)
        self.image_path_entry.insert(0, display_text)

        # 更新日志
        for path in self.image_paths:
            self.update_log(f"📷 已加载上次图片: {os.path.basename(path)}")

        # 更新预览
        self.update_preview()

        # 更新保存的配置（主要是更新时间戳或者确保一致性）
        if self.image_paths:
            self.last_image_dir = os.path.dirname(self.image_paths[0])
            self.save_last_session_config()

    def open_rule_manager(self):
        """打开规则管理器"""
        # 使用新版的规则管理器方法（如果存在）
        if hasattr(self, "open_rule_manager_window"):
            self.open_rule_manager_window()
        elif hasattr(self, "config"):
            # 直接复用 WechatRemarkModifier 中的实现逻辑
            try:
                rule_window = tk.Toplevel(self.root)
                rule_window.title("过滤规则管理")
                rule_window.geometry("800x600")
                rule_window.configure(bg="#F8F9FA")

                notebook = ttk.Notebook(rule_window)
                notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

                categories = [
                    (
                        "过滤关键词",
                        "filter_keywords",
                        "跳过发送：如果联系人名字或备注中包含这些词，程序将**直接跳过**该联系人，不发送任何消息。（例如：'老师', '客服'）",
                    ),
                    (
                        "内容清洗",
                        "clean_keywords",
                        "内容清洗：发送前会将名字中的这些词**删除**，但**不会跳过**该联系人。（例如：把'张三(云山)'清洗为'张三'）",
                    ),
                    (
                        "称谓去除",
                        "title_keywords",
                        "称谓去除：智能识别并去除名字中的称谓，只保留核心名字。（例如：'李明爸爸' -> '李明'，'王老师' -> '王'）",
                    ),
                    (
                        "年级去除",
                        "grade_keywords",
                        "年级去除：去除名字中包含的年级信息，避免称呼中带上班级。（例如：'初一张三' -> '张三'）",
                    ),
                    (
                        "干扰词库",
                        "noise_keywords",
                        "机构去噪：去除名字中混入的公司、机构、职业等干扰词。（例如：'张三(汇通金融)' -> '张三'）",
                    ),
                    (
                        "复姓库",
                        "compound_surnames",
                        "复姓识别：用于正确识别复姓（如欧阳、司马），确保截取名字时不会把姓氏截断。",
                    ),
                    (
                        "删除检测",
                        "delete_keywords",
                        "被删检测：程序通过检测聊天记录或错误信息中是否包含这些词，来判断是否被对方删除或拉黑。（例如：'开启了朋友验证'）",
                    ),
                    (
                        "无效关键词",
                        "invalid_keywords",
                        "无效名判定(包含)：如果清洗后的名字**包含**这些词，将被视为无效名字，发送时会使用通用称呼（如'朋友'）。（例如：'销售', '客服'）",
                    ),
                    (
                        "精准无效名",
                        "exact_match_invalid_names",
                        "无效名判定(完全匹配)：只有当清洗后的名字**完全等于**这些词时，才视为无效名字。（例如：名字只剩'初一'或'高三'时，视为无效）",
                    ),
                    (
                        "过滤字符",
                        "filter_chars",
                        "字符剔除：强制删除名字中的特定单个字符。（例如：删除名字里的'春','夏'等季节词）",
                    ),
                    (
                        "正则过滤",
                        "filter_patterns",
                        "正则清洗：使用正则表达式进行高级清洗。（例如：`初[0-9]+` 可以去除'初一'、'初12'等所有变体）",
                    ),
                ]

                self.rule_text_widgets = {}

                for title, key, desc in categories:
                    frame = ttk.Frame(notebook, padding=10)
                    notebook.add(frame, text=title)

                    ttk.Label(
                        frame,
                        text=f"说明：{desc}",
                        font=("Segoe UI", 10, "italic"),
                        foreground="#6B7280",
                    ).pack(anchor="w", pady=(0, 10))

                    text_area = scrolledtext.ScrolledText(
                        frame, width=60, height=15, font=("Segoe UI", 11)
                    )
                    text_area.pack(fill=tk.BOTH, expand=True)

                    current_rules = self.config.get(key, [])
                    text_area.insert(tk.END, "\n".join(current_rules))

                    self.rule_text_widgets[key] = text_area

                    btn_frame = ttk.Frame(frame)
                    btn_frame.pack(fill=tk.X, pady=10)
                    ttk.Button(
                        btn_frame,
                        text=f"保存 [{title}]",
                        command=lambda k=key: self.save_rule(k),
                    ).pack(side=tk.RIGHT)

            except Exception as e:
                messagebox.showerror("错误", f"打开规则管理失败: {e}")
        else:
            messagebox.showerror("错误", "配置未加载")

    def ask_yesno_safe(self, title, message, default=False):
        """线程安全地弹出 yes/no 确认框并返回结果"""
        if threading.current_thread() is threading.main_thread():
            return messagebox.askyesno(title, message)
        result = {"value": default}
        done = threading.Event()

        def _ask():
            try:
                result["value"] = messagebox.askyesno(title, message)
            finally:
                done.set()

        self.run_in_main_thread(_ask)
        while not done.is_set() and self.is_sending:
            time.sleep(0.1)
        return result["value"]

    def get_today_key(self):
        return datetime.now().strftime("%Y-%m-%d")

    def get_daily_sent_count(self):
        today = self.get_today_key()
        if self.config.get("daily_sent_date", "") != today:
            self.config["daily_sent_date"] = today
            self.config["daily_sent_count"] = 0
            self.save_app_config()
        return int(self.config.get("daily_sent_count", 0))

    def increment_daily_sent_count(self, step=1):
        step = max(0, int(step))
        if step == 0:
            return int(self.config.get("daily_sent_count", 0))
        current = self.get_daily_sent_count()
        self.config["daily_sent_count"] = current + step
        self.save_app_config()
        return self.config["daily_sent_count"]

    def update_status(self, text, color=None):
        """线程安全的更新状态栏 (Badge 样式)"""

        def _update():
            # 1. 侧边栏状态
            if hasattr(self, "status_label") and self.status_label.winfo_exists():
                # 判断组件类型
                if isinstance(self.status_label, ScrollingStatus):
                    # 映射状态类型
                    msg_type = "info"
                    if (
                        "成功" in text
                        or "完成" in text
                        or "在线" in text
                        or (color and "green" in str(color))
                    ):
                        msg_type = "success"
                    elif (
                        "错误" in text
                        or "失败" in text
                        or "停止" in text
                        or (color and "red" in str(color))
                    ):
                        msg_type = "error"
                    elif (
                        "暂停" in text
                        or "警告" in text
                        or (color and "orange" in str(color))
                    ):
                        msg_type = "warning"

                    self.status_label.set_message(text, msg_type)
                else:
                    # 旧版 Label 逻辑
                    self.status_label.config(text=f"● {text}")

                    # 根据文本或颜色决定样式
                    style = "BadgeInfo.TLabel"
                    if "成功" in text or "完成" in text or "在线" in text:
                        style = "BadgeSuccess.TLabel"
                    elif "错误" in text or "失败" in text or "停止" in text:
                        style = "BadgeError.TLabel"
                    elif "暂停" in text or "警告" in text:
                        style = "BadgeWarning.TLabel"

                    self.status_label.configure(style=style)

            # 2. 仪表盘状态
            if (
                hasattr(self, "dash_status_label")
                and self.dash_status_label.winfo_exists()
            ):
                self.dash_status_label.config(text=text)
                # 仪表盘状态也同步样式
                style = "Secondary.TLabel"  # 默认
                if "成功" in text:
                    style = "BadgeSuccess.TLabel"
                elif "错误" in text:
                    style = "BadgeError.TLabel"
                elif "运行" in text:
                    style = "BadgeInfo.TLabel"

                # 注意：dash_status_label 可能需要 padding 调整，这里简化处理
                self.dash_status_label.configure(
                    foreground=self.colors["text_secondary"]
                )  # 保持简洁，不一定用 badge 背景

        self.run_in_main_thread(_update)

    def update_progress(self, current, total):
        """线程安全的更新进度"""

        def _update():
            self.progress_var.set(f"{current}/{total}")

            # 更新所有进度条
            for pbar_name in ["progress_bar", "dash_progress", "config_progress"]:
                if hasattr(self, pbar_name):
                    pbar = getattr(self, pbar_name)
                    if pbar and pbar.winfo_exists():
                        pbar["maximum"] = total
                        pbar["value"] = current

            # 计算预计剩余时间
            if current > 0 and hasattr(self, "start_time") and self.start_time:
                elapsed_time = time.time() - self.start_time
                avg_time_per_item = elapsed_time / current
                remaining_items = total - current
                remaining_time = remaining_items * avg_time_per_item

                # 格式化时间
                if remaining_time < 60:
                    time_str = f"{int(remaining_time)}秒"
                else:
                    minutes = int(remaining_time // 60)
                    seconds = int(remaining_time % 60)
                    time_str = f"{minutes}分{seconds}秒"

                if hasattr(self, "time_remaining_var"):
                    self.time_remaining_var.set(f"预计剩余时间: {time_str}")
            elif hasattr(self, "time_remaining_var"):
                if current == 0:
                    self.time_remaining_var.set("预计剩余时间: --:--")
                else:
                    self.time_remaining_var.set("预计剩余时间: 计算中...")

            # 实时更新结果页面 (如果当前处于结果页)
            if hasattr(self, "current_page_id") and self.current_page_id == "results":
                self.update_results_view()

        self.run_in_main_thread(_update)

    def create_card_frame(self, parent, title):
        """创建卡片式容器 (兼容旧方法名，实际使用 setup_scrollable_ui 中的样式)"""
        # 卡片容器
        card = ttk.Frame(parent, style="Card.TFrame", padding=15)

        # 标题栏
        if title:
            # 使用 Segoe UI 字体
            ttk.Label(
                card, text=title, font=("Segoe UI", 12, "bold"), background="#FFFFFF"
            ).pack(anchor="w", pady=(0, 15))

        return card

    def create_marquee(
        self, parent, text, bg="white", fg="#FF4D4F", font=("Segoe UI", 10), height=30
    ):
        """创建滚动字幕（改进版：自动测量文本宽度，强制滚动）"""
        frame = tk.Frame(parent, bg=bg, height=height)
        frame.pack_propagate(False)

        canvas = tk.Canvas(frame, bg=bg, height=height, highlightthickness=0)
        canvas.pack(fill=tk.BOTH, expand=True)

        # 创建文本对象
        text_id = canvas.create_text(
            0, height / 2, text=text, font=font, fill=fg, anchor="w"
        )

        # 初始位置：从右侧开始
        canvas_width = 1000  # 初始假设宽度，后续会动态获取
        canvas.coords(text_id, canvas_width, height / 2)

        def scroll():
            try:
                if not frame.winfo_exists():
                    return

                # 获取实时宽度
                current_canvas_width = frame.winfo_width()
                if current_canvas_width <= 1:
                    frame.after(100, scroll)
                    return

                # 获取文本当前位置
                bbox = canvas.bbox(text_id)
                if not bbox:  # 尚未绘制完成
                    frame.after(100, scroll)
                    return

                text_width = bbox[2] - bbox[0]

                # 移动步长
                canvas.move(text_id, -1.5, 0)  # 稍微调快一点点

                # 检查是否完全移出左侧
                if bbox[2] < 0:
                    # 重置到右侧边缘
                    canvas.coords(text_id, current_canvas_width, height / 2)

                frame.after(20, scroll)  # 刷新频率
            except Exception:
                pass

        # 启动滚动
        frame.after(100, scroll)
        return frame

    def setup_scrollable_ui(self):
        """
        现代 SaaS 仪表盘 V4 - Design System Hardening
        目标: 提升控制感、层级清晰度和产品成熟度
        """
        # --- 1. 设计系统 V4 ---
        self.style = ttk.Style()
        self.style.theme_use("clam")

        # 现代企业色板 (高对比度/层级明确)
        # 杂志编辑风配色 (纸张质感)
        self.colors = {
            "bg_app": "#F4EFE6",        # 纸张背景
            "bg_sidebar": "#EDE7DB",    # 侧边栏米色
            "bg_card": "#FBF8F2",       # 卡片暖白
            "bg_card_hover": "#F5F0E6", # 卡片悬停
            "primary": "#8B2500",       # 深红主色 (杂志红)
            "primary_hover": "#A03000",
            "primary_active": "#702000",
            "primary_text": "#FBF8F2",  # 暖白文字
            "secondary": "#E8E0D4",     # 次要按钮
            "secondary_border": "#C4B9A8",
            "secondary_text": "#4A4A44",
            "secondary_hover": "#DDD5C8",
            "danger": "#C53030",        # 深红
            "danger_hover": "#9B2424",
            "success": "#2D6A4F",       # 深绿
            "success_hover": "#1B4332",
            "warning": "#B45309",       # 深橙
            "text_main": "#1A1A18",     # 近黑墨色
            "text_secondary": "#6B6B60", # 灰色
            "text_tertiary": "#9C9C8E", # 浅灰
            "text_muted": "#B8B8A8",    # 弱化文字
            "text_disabled": "#C4C4B8",
            "border": "#C4B9A8",        # 边框米灰
            "sidebar_border": "#D4CAB8",
            # 状态色 (Badge)
            "status_success_bg": "#D4EDDA",
            "status_success_fg": "#1B4332",
            "status_warning_bg": "#FFF3CD",
            "status_warning_fg": "#664D03",
            "status_error_bg": "#F8D7DA",
            "status_error_fg": "#842029",
            "status_info_bg": "#D1ECF1",
            "status_info_fg": "#0C5460",
            "nav_hover": "#E8E0D4",
            "nav_active": "#FBF8F2",
            "nav_active_bar": "#8B2500",
        }

        # 层级优先排版 (强化对比)
        self.fonts = {
            "display": ("Segoe UI", 36, "bold"),  # KPI 数字 (显著放大 >= 28px)
            "h1": ("Segoe UI", 24, "bold"),  # 页面标题
            "h2": ("Segoe UI", 16, "bold"),  # 模块标题 (与正文拉开等级)
            "h3": ("Segoe UI", 14, "bold"),  # 卡片/分组标题
            "body": ("Segoe UI", 10),  # 正文
            "body_bold": ("Segoe UI", 10, "bold"),
            "small": ("Segoe UI", 9),  # 辅助信息
            "nav": ("Segoe UI", 10),  # 导航默认
            "nav_active": ("Segoe UI", 10, "bold"),  # 导航激活 (加粗)
            "badge": ("Segoe UI", 9, "bold"),  # 状态徽章
        }

        # 宽敞间距系统 (提升呼吸感)
        self.spacing = {
            "xs": 4,
            "sm": 8,
            "md": 16,
            "lg": 24,  # 卡片内边距
            "xl": 32,  # 模块间距
            "xxl": 48,
        }

        # --- 2. 配置样式 ---
        self._configure_styles()

        # --- 3. 布局 ---
        self.root.configure(bg=self.colors["bg_app"])
        self.root.geometry("1440x900")

        # 状态管理 (提前初始化)
        self.pages = {}
        self.current_page = None
        self.nav_buttons = {}
        self.current_page_id = None

        # 根容器
        root_container = tk.Frame(self.root, bg=self.colors["bg_app"])
        root_container.pack(fill=tk.BOTH, expand=True)

        # A. 侧边栏 (左侧固定, 白色, 带边框)
        self.sidebar_frame = tk.Frame(
            root_container, bg=self.colors["bg_sidebar"], width=260
        )
        self.sidebar_frame.pack(side=tk.LEFT, fill=tk.Y)
        self.sidebar_frame.pack_propagate(False)

        # 侧边栏右边框 (已移除 sidebar_border, 使用 bg_sidebar 或 border)
        # 现代化设计中，深色侧边栏通常不需要明显的右边框，或者使用 border 颜色
        # 这里直接移除，或者改为 border
        # tk.Frame(root_container, bg=self.colors['border'], width=1).pack(side=tk.LEFT, fill=tk.Y)

        self._init_sidebar(self.sidebar_frame)

        # B. 内容区域
        self.content_area = tk.Frame(root_container, bg=self.colors["bg_app"])
        self.content_area.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 页面容器 (增加外边距)
        self.page_container = tk.Frame(self.content_area, bg=self.colors["bg_app"])
        self.page_container.pack(
            fill=tk.BOTH, expand=True, padx=self.spacing["xl"], pady=self.spacing["xl"]
        )

        # 初始化页面
        self._init_pages()

        # 显示默认页
        self.show_page("dashboard")

    def _configure_styles(self):
        """配置全局样式"""
        self.style = ttk.Style()
        self.style.theme_use("clam")

        # --- 2. 字体配置 ---
        self.fonts = {
            "display": ("Segoe UI", 32, "bold"),
            "h1": ("Segoe UI", 20, "bold"),
            "h2": ("Segoe UI", 14, "bold"),
            "h3": ("Segoe UI", 12, "bold"),
            "body": ("Segoe UI", 10),
            "body_bold": ("Segoe UI", 10, "bold"),
            "body_medium": ("Segoe UI", 10, "bold"),  # 兼容
            "small": ("Segoe UI", 9),
            "nav": ("Segoe UI", 10),
            "nav_active": ("Segoe UI", 10, "bold"),
            "badge": ("Segoe UI", 9, "bold"),
        }

        # 间距系统
        self.spacing = {"xs": 4, "sm": 8, "md": 16, "lg": 24, "xl": 32, "xxl": 48}

        # --- 3. 组件样式配置 ---

        # 全局默认
        self.style.configure(
            ".",
            background=self.colors["bg_app"],
            foreground=self.colors["text_main"],
            font=self.fonts["body"],
        )

        # 卡片 Frame
        self.style.configure(
            "Card.TFrame", background=self.colors["bg_card"], relief="flat"
        )

        # 标签 Label
        self.style.configure(
            "H1.TLabel",
            font=self.fonts["h1"],
            background=self.colors["bg_app"],
            foreground=self.colors["text_main"],
        )
        self.style.configure(
            "H2.TLabel",
            font=self.fonts["h2"],
            background=self.colors["bg_card"],
            foreground=self.colors["text_main"],
        )
        self.style.configure(
            "Secondary.TLabel",
            font=self.fonts["small"],
            background=self.colors["bg_card"],
            foreground=self.colors["text_secondary"],
        )
        self.style.configure(
            "SecondaryApp.TLabel",
            font=self.fonts["body"],
            background=self.colors["bg_app"],
            foreground=self.colors["text_secondary"],
        )
        self.style.configure(
            "Display.TLabel",
            font=self.fonts["display"],
            background=self.colors["bg_card"],
            foreground=self.colors["text_main"],
        )

        # 按钮 Button
        # Primary
        self.style.configure(
            "Primary.TButton",
            font=self.fonts["body_medium"],
            padding=(20, 10),
            borderwidth=0,
            background=self.colors["primary"],
            foreground="white",
        )
        self.style.map(
            "Primary.TButton",
            background=[
                ("active", self.colors["primary_hover"]),
                ("disabled", self.colors["border"]),
            ],
            foreground=[("disabled", "white")],
        )

        # Secondary
        self.style.configure(
            "Secondary.TButton",
            font=self.fonts["body"],
            padding=(16, 8),
            borderwidth=1,
            relief="solid",
            background=self.colors["secondary"],
            foreground=self.colors["text_main"],
            bordercolor=self.colors["secondary_border"],
        )
        self.style.map(
            "Secondary.TButton",
            background=[("active", self.colors["secondary_hover"])],
            foreground=[("active", self.colors["primary"])],
            bordercolor=[("active", self.colors["primary"])],
        )

        # Danger
        self.style.configure(
            "Danger.TButton",
            font=self.fonts["body_medium"],
            padding=(16, 8),
            borderwidth=0,
            background=self.colors["danger"],
            foreground="white",
        )
        self.style.map("Danger.TButton", background=[("active", "#DC2626")])

        # Treeview
        self.style.configure(
            "Treeview",
            background=self.colors["bg_card"],
            foreground=self.colors["text_main"],
            fieldbackground=self.colors["bg_card"],
            rowheight=36,
            font=self.fonts["body"],
            borderwidth=0,
        )
        self.style.configure(
            "Treeview.Heading",
            background=self.colors["secondary"],
            foreground=self.colors["text_secondary"],
            font=self.fonts["body_medium"],
            padding=(12, 10),
            relief="flat",
        )
        self.style.map(
            "Treeview",
            background=[("selected", self.colors["nav_active"])],
            foreground=[("selected", self.colors["primary"])],
        )

        # Notebook
        self.style.configure(
            "TNotebook", background=self.colors["bg_app"], borderwidth=0
        )
        self.style.configure(
            "TNotebook.Tab",
            padding=(16, 12),
            font=self.fonts["body"],
            background=self.colors["bg_app"],
            borderwidth=0,
        )
        self.style.map(
            "TNotebook.Tab",
            background=[("selected", self.colors["bg_card"])],
            foreground=[("selected", self.colors["primary"])],
        )

        # Badge Labels
        self.style.configure(
            "BadgeInfo.TLabel",
            font=self.fonts["badge"],
            foreground=self.colors["status_info_fg"],
            background=self.colors["status_info_bg"],
            padding=4,
        )
        self.style.configure(
            "BadgeSuccess.TLabel",
            font=self.fonts["badge"],
            foreground=self.colors["status_success_fg"],
            background=self.colors["status_success_bg"],
            padding=4,
        )
        self.style.configure(
            "BadgeWarning.TLabel",
            font=self.fonts["badge"],
            foreground=self.colors["status_warning_fg"],
            background=self.colors["status_warning_bg"],
            padding=4,
        )
        self.style.configure(
            "BadgeError.TLabel",
            font=self.fonts["badge"],
            foreground=self.colors["status_error_fg"],
            background=self.colors["status_error_bg"],
            padding=4,
        )

        # 进度条
        self.style.configure(
            "Horizontal.TProgressbar",
            background=self.colors["primary"],
            troughcolor=self.colors["border"],
            thickness=8,
            borderwidth=0,
        )

    def _init_sidebar(self, parent):
        """杂志风格侧边栏 - 优雅简约"""
        # Logo 区域
        logo_box = tk.Frame(parent, bg=self.colors["bg_sidebar"], height=80)
        logo_box.pack(fill=tk.X, padx=24, pady=(28, 20))
        logo_box.pack_propagate(False)

        # Logo 图标（简约风格）
        logo_icon = tk.Label(
            logo_box,
            text="W",
            font=("Georgia", 28, "bold"),
            bg=self.colors["bg_sidebar"],
            fg=self.colors["primary"],
        )
        logo_icon.pack(side=tk.LEFT)

        # Logo 文字
        logo_text = tk.Frame(logo_box, bg=self.colors["bg_sidebar"])
        logo_text.pack(side=tk.LEFT, padx=(12, 0))

        tk.Label(
            logo_text,
            text="WeChat",
            font=("Georgia", 18, "bold"),
            bg=self.colors["bg_sidebar"],
            fg=self.colors["text_main"],
        ).pack(anchor="w")
        tk.Label(
            logo_text,
            text="Auto Sender",
            font=("Georgia", 9),
            bg=self.colors["bg_sidebar"],
            fg=self.colors["text_secondary"],
        ).pack(anchor="w")

        # 分隔线
        tk.Frame(parent, bg=self.colors["border"], height=1).pack(fill=tk.X, padx=24)

        # 导航菜单
        nav_box = tk.Frame(parent, bg=self.colors["bg_sidebar"])
        nav_box.pack(fill=tk.BOTH, expand=True, pady=20, padx=16)

        # 导航项配置（纯文字，无图标）
        menu_items = [
            ("dashboard", "仪表盘"),
            ("config", "任务配置"),
            ("results", "发送结果"),
            ("contacts", "联系人管理"),
            ("rules", "规则管理"),
            ("logs", "系统日志"),
        ]

        self.nav_buttons = {}

        for page_id, text in menu_items:
            # 导航按钮容器
            btn_frame = tk.Frame(
                nav_box, bg=self.colors["bg_sidebar"], cursor="hand2", height=40
            )
            btn_frame.pack(fill=tk.X, pady=1)
            btn_frame.pack_propagate(False)

            # 左侧指示条（默认隐藏）
            indicator = tk.Frame(btn_frame, bg=self.colors["bg_sidebar"], width=3)
            indicator.pack(side=tk.LEFT, fill=tk.Y)

            # 文字（居中，无图标）
            lbl = tk.Label(
                btn_frame,
                text=text,
                font=("Georgia", 10),
                bg=self.colors["bg_sidebar"],
                fg=self.colors["text_secondary"],
                anchor="w",
            )
            lbl.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(16, 0))

            # 存储引用
            self.nav_buttons[page_id] = {
                "frame": btn_frame,
                "indicator": indicator,
                "label": lbl,
            }

            # 绑定事件
            for widget in [btn_frame, lbl, indicator]:
                widget.bind("<Button-1>", lambda e, pid=page_id: self.show_page(pid))
                widget.bind(
                    "<Enter>", lambda e, pid=page_id: self._on_nav_hover(pid, True)
                )
                widget.bind(
                    "<Leave>", lambda e, pid=page_id: self._on_nav_hover(pid, False)
                )

            # 存储引用
            self.nav_buttons[page_id] = {
                "frame": btn_frame,
                "indicator": indicator,
                "label": lbl,
            }

            # 绑定事件
            for widget in [btn_frame, lbl, indicator]:
                widget.bind("<Button-1>", lambda e, pid=page_id: self.show_page(pid))
                widget.bind(
                    "<Enter>", lambda e, pid=page_id: self._on_nav_hover(pid, True)
                )
                widget.bind(
                    "<Leave>", lambda e, pid=page_id: self._on_nav_hover(pid, False)
                )

        # 用户资料 (底部)
        user_box = tk.Frame(parent, bg=self.colors["bg_sidebar"], height=80)
        user_box.pack(side=tk.BOTTOM, fill=tk.X, padx=24, pady=20)

        tk.Label(
            user_box,
            text="管理员",
            font=("Georgia", 10, "bold"),
            bg=self.colors["bg_sidebar"],
            fg=self.colors["text_main"],
        ).pack(anchor="w")

        # 状态指示 (改为滚动)
        status_row = tk.Frame(user_box, bg=self.colors["bg_sidebar"])
        status_row.pack(anchor="w", pady=(8, 0), fill=tk.X)

        self.status_label = ScrollingStatus(
            status_row,
            bg=self.colors["status_success_bg"],
            fg=self.colors["status_success_fg"],
            font=("Georgia", 9),
            height=24
        )
        self.status_label.pack(fill=tk.X)
        self.status_label.set_message("正在初始化...", "info")

    def _on_nav_hover(self, page_id, is_hover):
        """导航悬停效果 - 平滑过渡"""
        if self.current_page_id == page_id:
            return

        widgets = self.nav_buttons[page_id]

        if is_hover:
            bg = self.colors["nav_hover"]
            fg = self.colors["text_main"]
        else:
            bg = self.colors["bg_sidebar"]
            fg = self.colors["text_secondary"]

        widgets["frame"].configure(bg=bg)
        widgets["label"].configure(bg=bg, fg=fg)
        widgets["indicator"].configure(bg=bg)

    def show_page(self, page_id):
        """切换页面"""
        self.current_page_id = page_id

        # 更新导航状态
        for pid, widgets in self.nav_buttons.items():
            if pid == page_id:
                # 激活状态
                widgets["frame"].configure(bg=self.colors["nav_active"])
                widgets["label"].configure(
                    bg=self.colors["nav_active"], fg=self.colors["primary"],
                    font=("Georgia", 10, "bold")
                )
                widgets["indicator"].configure(bg=self.colors["nav_active_bar"])
            else:
                # 未激活状态
                widgets["frame"].configure(bg=self.colors["bg_sidebar"])
                widgets["label"].configure(
                    bg=self.colors["bg_sidebar"], fg=self.colors["text_secondary"],
                    font=("Georgia", 10)
                )
                widgets["indicator"].configure(bg=self.colors["bg_sidebar"])

        # 切换视图
        if self.current_page:
            self.current_page.pack_forget()

        if page_id in self.pages:
            self.current_page = self.pages[page_id]
            self.current_page.pack(fill=tk.BOTH, expand=True)

            if page_id == "dashboard":
                self.update_dashboard_kpi()

    def _create_card(self, parent, title=None, padding=None, width=None):
        """创建现代化圆角卡片 (模拟)"""
        # 外层容器 - 用于间距
        wrapper = tk.Frame(parent, bg=self.colors["bg_app"])
        wrapper.pack(fill=tk.X, pady=(0, 16))

        # 实际卡片 - 白色背景 + 边框模拟
        # Tkinter Frame 不支持圆角，使用 highlightthickness 模拟边框
        pad = padding if padding else 20
        card = tk.Frame(wrapper, bg=self.colors["bg_card"], padx=pad, pady=pad)

        if width:
            card.pack(
                fill=tk.X, expand=True
            )  # width 在 pack 布局中通常由 fill 控制，或者 frame 内部 propagate
            # 如果需要固定宽度，可能需要 propagate(False)
        else:
            card.pack(fill=tk.BOTH, expand=True)

        # 边框效果
        card.config(
            highlightbackground=self.colors["border"],
            highlightthickness=1,
            highlightcolor=self.colors["border"],
        )

        # 标题
        if title:
            header = tk.Frame(card, bg=self.colors["bg_card"])
            header.pack(fill=tk.X, pady=(0, 16))

            title_label = tk.Label(
                header,
                text=title,
                font=self.fonts["h2"],
                bg=self.colors["bg_card"],
                fg=self.colors["text_main"],
            )
            title_label.pack(side=tk.LEFT)

            # 装饰线
            line = tk.Frame(card, bg=self.colors["secondary_border"], height=1)
            line.pack(fill=tk.X, pady=(0, 16))

        return card

    def _init_pages(self):
        """初始化所有视图框架"""
        # 1. 仪表盘页面
        self.pages["dashboard"] = self._create_dashboard_view()

        # 2. 配置页面 (原左侧面板)
        self.pages["config"] = self._create_config_view()

        # 3. 结果页面
        self.pages["results"] = self._create_results_view()

        # 4. 联系人页面 (原右侧面板标签 1)
        self.pages["contacts"] = self._create_contacts_view()

        # 5. 规则管理页面 (新增)
        self.pages["rules"] = self._create_rules_view()

        # 6. 日志页面 (原右侧面板标签 2)
        self.pages["logs"] = self._create_logs_view()

    def _create_scrollable_panel(self, parent, width):
        """创建可滚动的面板容器"""
        # 外层 Frame 用于放置 Canvas 和 Scrollbar
        container = tk.Frame(parent, bg=self.colors["bg_app"])

        canvas = tk.Canvas(container, bg=self.colors["bg_app"], highlightthickness=0)
        # 隐藏滚动条 (SaaS 风格通常隐藏非必要滚动条，或者美化它)
        scrollbar = tk.Scrollbar(container, orient="vertical", command=canvas.yview)

        scrollable_frame = ttk.Frame(canvas, style="App.TFrame")

        scrollable_frame.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        window_id = canvas.create_window(
            (0, 0), window=scrollable_frame, anchor="nw", width=width - 20
        )

        # 动态调整宽度
        def on_canvas_resize(event):
            canvas.itemconfig(window_id, width=event.width)

        canvas.bind("<Configure>", on_canvas_resize)
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 绑定鼠标滚轮
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        # 绑定到 canvas 和 frame 上，确保鼠标在哪里都能滚
        # 注意：全局绑定可能会冲突，最好只在 enter/leave 时绑定
        def _bind_mousewheel(event):
            canvas.bind_all("<MouseWheel>", _on_mousewheel)

        def _unbind_mousewheel(event):
            canvas.unbind_all("<MouseWheel>")

        container.bind("<Enter>", _bind_mousewheel)
        container.bind("<Leave>", _unbind_mousewheel)

        return container, scrollable_frame  # 返回外层容器和内部 Frame

    def update_dashboard_kpi(self):
        """更新仪表盘数字"""
        if hasattr(self, "kpi_contacts"):  # 检查仪表盘是否已初始化
            if hasattr(self, "contacts"):
                self.kpi_contacts.set(str(len(self.contacts)))

            if hasattr(self, "send_results"):
                sent = len(self.send_results.get("success", []))
                failed = len(self.send_results.get("failed", []))
                total_sent = sent + failed
                self.kpi_sent.set(str(total_sent))

                if total_sent > 0:
                    rate = (sent / total_sent) * 100
                    self.kpi_rate.set(f"{rate:.1f}%")
                else:
                    self.kpi_rate.set("0%")

            if hasattr(self, "time_remaining_var"):
                time_val = self.time_remaining_var.get().replace("预计剩余: ", "")
                if time_val:
                    self.kpi_time.set(time_val)

        # 更新仪表盘状态栏
        if hasattr(self, "dash_status_label"):
            if self.is_initialized:
                self.dash_status_label.config(text="系统就绪", style="BadgeInfo.TLabel")
            else:
                self.dash_status_label.config(
                    text="微信未连接", style="BadgeError.TLabel"
                )

    def _create_config_view(self):
        """重新实现配置视图，使用原有逻辑但适配全屏页面"""
        # 确保页面已定义
        page = tk.Frame(self.page_container, bg=self.colors["bg_app"])

        # Header
        header = tk.Frame(page, bg=self.colors["bg_app"])
        header.pack(fill=tk.X, pady=(0, 24))
        ttk.Label(header, text="任务配置", style="H1.TLabel").pack(anchor="w")

        # 通过创建包装器复用现有的填充逻辑
        # 配置内容较多，需要可滚动的包装器
        container, scroll_frame = self._create_scrollable_panel(page, width=800)
        container.pack(fill=tk.BOTH, expand=True)

        # 使用临时占位符将 _fill_left_panel 指向此框架
        original_left_panel = self.left_panel if hasattr(self, "left_panel") else None
        self.left_panel = scroll_frame  # 劫持
        self._fill_left_panel()  # 复用现有方法

        # 恢复（虽然在新架构中严格来说不需要）
        # self.left_panel = original_left_panel

        return page

    def _create_results_view(self):
        """创建发送结果页面 (集成版)"""
        # 确保页面已定义
        page = tk.Frame(self.page_container, bg=self.colors["bg_app"])

        # Header
        header = tk.Frame(page, bg=self.colors["bg_app"])
        header.pack(fill=tk.X, pady=(0, 24))

        title_block = tk.Frame(header, bg=self.colors["bg_app"])
        title_block.pack(side=tk.LEFT)
        ttk.Label(title_block, text="发送结果", style="H1.TLabel").pack(anchor="w")
        ttk.Label(
            title_block, text="实时任务执行统计与详情", style="SecondaryApp.TLabel"
        ).pack(anchor="w")

        # 操作栏
        actions_block = tk.Frame(header, bg=self.colors["bg_app"])
        actions_block.pack(side=tk.RIGHT, anchor="e")
        ttk.Button(
            actions_block,
            text="🔄 刷新数据",
            style="Secondary.TButton",
            command=self.update_results_view,
        ).pack(side=tk.LEFT, padx=(0, 12))
        ttk.Button(
            actions_block,
            text="💾 导出报告",
            style="Secondary.TButton",
            command=lambda: self.save_results(page),
        ).pack(side=tk.LEFT)

        # KPI 统计卡片
        kpi_row = tk.Frame(page, bg=self.colors["bg_app"])
        kpi_row.pack(fill=tk.X, pady=(0, self.spacing["xl"]))

        self.res_total = tk.StringVar(value="0")
        self.res_success = tk.StringVar(value="0")
        self.res_failed = tk.StringVar(value="0")
        self.res_deleted = tk.StringVar(value="0")

        def add_res_kpi(parent, title, value_var, color_style=None, is_last=False):
            container = tk.Frame(parent, bg=self.colors["bg_app"])
            container.pack(
                side=tk.LEFT,
                fill=tk.BOTH,
                expand=True,
                padx=(0, 0 if is_last else self.spacing["lg"]),
            )

            card = ttk.Frame(container, style="Card.TFrame", padding=self.spacing["lg"])
            card.pack(fill=tk.BOTH, expand=True)

            ttk.Label(card, text=title, style="Secondary.TLabel").pack(anchor="w")

            # 自定义颜色 Label
            val_label = ttk.Label(
                card,
                textvariable=value_var,
                font=self.fonts["display"],
                background=self.colors["bg_card"],
            )
            if color_style:
                val_label.configure(foreground=color_style)
            val_label.pack(anchor="w", pady=(12, 8))

        add_res_kpi(kpi_row, "总计尝试", self.res_total)
        add_res_kpi(
            kpi_row,
            "发送成功",
            self.res_success,
            color_style=self.colors["status_success_fg"],
        )
        add_res_kpi(kpi_row, "发送失败", self.res_failed)

        # 删除率计算 (新增)
        self.res_deleted_rate = tk.StringVar(value="0%")

        # 发现被删 + 删除率
        container = tk.Frame(kpi_row, bg=self.colors["bg_app"])
        container.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        card = ttk.Frame(container, style="Card.TFrame", padding=self.spacing["lg"])
        card.pack(fill=tk.BOTH, expand=True)

        ttk.Label(card, text="发现被删", style="Secondary.TLabel").pack(anchor="w")

        row_val = tk.Frame(card, bg=self.colors["bg_card"])
        row_val.pack(anchor="w", pady=(12, 8))

        # 数字 (红色)
        val_label = ttk.Label(
            row_val,
            textvariable=self.res_deleted,
            font=self.fonts["display"],
            background=self.colors["bg_card"],
            foreground=self.colors["danger"],
        )
        val_label.pack(side=tk.LEFT)

        # 删除率 (红色, 字体稍小)
        rate_label = ttk.Label(
            row_val,
            textvariable=self.res_deleted_rate,
            font=("Segoe UI", 12, "bold"),
            background=self.colors["bg_card"],
            foreground=self.colors["danger"],
        )
        rate_label.pack(side=tk.LEFT, padx=(8, 0), pady=(12, 0))  # 对齐底部

        # 详情列表 (Tabs)
        tab_card = self._create_card(page, padding=0)  # 无内边距以贴合 Notebook
        tab_card.pack(fill=tk.BOTH, expand=True)

        self.res_notebook = ttk.Notebook(tab_card)
        self.res_notebook.pack(
            fill=tk.BOTH, expand=True, padx=self.spacing["md"], pady=self.spacing["md"]
        )

        # 成功列表
        self.tree_success = self._create_result_tree(
            self.res_notebook, "发送成功", ["姓名", "备注", "原始昵称"]
        )

        # 失败列表
        self.tree_failed = self._create_result_tree(
            self.res_notebook, "发送失败", ["姓名", "原因", "备注"]
        )

        # 被删列表
        self.tree_deleted = self._create_result_tree(
            self.res_notebook, "被删/拉黑", ["姓名", "检测结果", "详情"]
        )

        return page

    def _create_result_tree(self, parent, title, columns):
        frame = ttk.Frame(parent)
        parent.add(frame, text=f"  {title}  ")

        tree = ttk.Treeview(
            frame, columns=columns, show="headings", selectmode="browse"
        )

        for col in columns:
            tree.heading(col, text=col)
            tree.column(col, width=150)

        scrollbar = tk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)

        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        return tree

    def update_results_view(self):
        """更新结果页面数据"""
        if not hasattr(self, "send_results"):
            return

        # 使用列表副本以避免多线程迭代问题
        success = list(self.send_results.get("success", []))
        failed = list(self.send_results.get("failed", []))
        deleted = list(self.send_results.get("deleted", []))

        # 总计尝试 = 成功 + 失败 + 被删
        total = len(success) + len(failed) + len(deleted)

        if hasattr(self, "res_total"):
            self.res_total.set(str(total))
            self.res_success.set(str(len(success)))
            self.res_failed.set(str(len(failed)))
            self.res_deleted.set(str(len(deleted)))

            # 计算并显示删除率 (基于总处理人数计算，即 成功+失败+被删)
            processed_total = len(success) + len(failed) + len(deleted)
            if processed_total > 0:
                rate = (len(deleted) / processed_total) * 100
                self.res_deleted_rate.set(f"({rate:.1f}%)")
            else:
                self.res_deleted_rate.set("(0.0%)")

        # 更新列表 (全量刷新，简单粗暴但有效)
        # 成功列表
        if hasattr(self, "tree_success"):
            self.tree_success.delete(*self.tree_success.get_children())
            for item in success:
                name = item.get("name", "")
                remark = item.get("remark", "")
                orig = item.get("original_display", "")
                self.tree_success.insert("", tk.END, values=(name, remark, orig))

        # 失败列表
        if hasattr(self, "tree_failed"):
            self.tree_failed.delete(*self.tree_failed.get_children())
            for item in failed:
                contact = item.get("contact", {})
                reason = item.get("reason", "")
                name = contact.get("name", "")
                remark = contact.get("remark", "")
                self.tree_failed.insert("", tk.END, values=(name, reason, remark))

        # 被删列表
        if hasattr(self, "tree_deleted"):
            self.tree_deleted.delete(*self.tree_deleted.get_children())
            for item in deleted:
                name = item.get("name", "")
                info = item.get("deletion_info", {})
                reason = info.get("reason", "未知")
                detail = f"{info.get('detection_method', '')}"
                self.tree_deleted.insert("", tk.END, values=(name, reason, detail))

    def _create_contacts_view(self):
        """联系人视图"""
        # 确保页面已定义
        page = tk.Frame(self.page_container, bg=self.colors["bg_app"])

        # 复用 _init_contacts_tab 逻辑
        self._init_contacts_tab(page)
        return page

    def _create_logs_view(self):
        """日志视图"""
        # 确保页面已定义
        page = tk.Frame(self.page_container, bg=self.colors["bg_app"])

        # 复用 _init_logs_tab 逻辑
        self._init_logs_tab(page)
        return page

    def _create_kpi_card(
        self, parent, title, value_var, subtext, col_idx, color=None, icon="📊"
    ):
        """创建美观的 KPI 卡片"""
        # 间距容器
        container = tk.Frame(parent, bg=self.colors["bg_app"])
        container.pack(
            side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 16) if col_idx < 3 else 0
        )

        # 卡片
        card = tk.Frame(container, bg=self.colors["bg_card"], padx=20, pady=20)
        card.pack(fill=tk.BOTH, expand=True)
        card.config(highlightbackground=self.colors["border"], highlightthickness=1)

        # 顶部：图标和标题
        header = tk.Frame(card, bg=self.colors["bg_card"])
        header.pack(fill=tk.X)

        # 图标背景
        icon_bg = tk.Frame(
            header,
            bg=color if color else self.colors["nav_active"],
            width=40,
            height=40,
        )
        icon_bg.pack(side=tk.LEFT)
        icon_bg.pack_propagate(False)

        icon_label = tk.Label(
            icon_bg,
            text=icon,
            font=("Segoe UI", 18),
            bg=color if color else self.colors["nav_active"],
            fg=self.colors["primary"] if not color else "white",
        )  # 调整图标颜色
        if color:
            icon_label.config(fg="white")

        icon_label.place(relx=0.5, rely=0.5, anchor="center")

        # 标题
        title_label = tk.Label(
            header,
            text=title,
            font=self.fonts["small"],
            bg=self.colors["bg_card"],
            fg=self.colors["text_secondary"],
        )
        title_label.pack(side=tk.LEFT, padx=(12, 0))

        # 数值
        val_label = tk.Label(
            card,
            textvariable=value_var,
            font=self.fonts["display"],
            bg=self.colors["bg_card"],
            fg=self.colors["text_main"],
        )
        val_label.pack(anchor="w", pady=(16, 4))

        # 副标题
        sub_label = tk.Label(
            card,
            text=subtext,
            font=self.fonts["small"],
            bg=self.colors["bg_card"],
            fg=self.colors["text_muted"],
        )
        sub_label.pack(anchor="w")

        # 底部装饰条
        bottom_bar = tk.Frame(
            card, bg=color if color else self.colors["primary"], height=3
        )
        bottom_bar.pack(fill=tk.X, pady=(16, 0), side=tk.BOTTOM)

    def _create_dashboard_view(self):
        # 防御性检查和页面初始化
        if not hasattr(self, "page_container"):
            print("Error: page_container missing")
            return None
        page = tk.Frame(self.page_container, bg=self.colors["bg_app"])

        # --- 头部区域 (极简) ---
        header_row = tk.Frame(page, bg=self.colors["bg_app"])
        header_row.pack(fill=tk.X, pady=(0, self.spacing["xl"]))

        # 左侧：标题
        title_block = tk.Frame(header_row, bg=self.colors["bg_app"])
        title_block.pack(side=tk.LEFT)
        ttk.Label(title_block, text="Overview", style="H1.TLabel").pack(anchor="w")
        ttk.Label(
            title_block, text="自动化营销实时监控", style="SecondaryApp.TLabel"
        ).pack(anchor="w")

        # 右侧：主要操作
        actions_block = tk.Frame(header_row, bg=self.colors["bg_app"])
        actions_block.pack(side=tk.RIGHT, anchor="e")

        ttk.Button(
            actions_block,
            text="新建任务",
            style="Primary.TButton",
            command=lambda: self.show_page("config"),
        ).pack(side=tk.LEFT)

        # --- KPI 区域 (强化数字层级) ---
        kpi_row = tk.Frame(page, bg=self.colors["bg_app"])
        kpi_row.pack(fill=tk.X, pady=(0, self.spacing["xl"]))

        self.kpi_contacts = tk.StringVar(value="0")
        self.kpi_sent = tk.StringVar(value="0")
        self.kpi_rate = tk.StringVar(value="0%")
        self.kpi_time = tk.StringVar(value="--")

        self._create_kpi_card(
            kpi_row, "Total Contacts", self.kpi_contacts, "数据库总量", 0, icon="👥"
        )
        self._create_kpi_card(
            kpi_row, "Sent Today", self.kpi_sent, "今日投递", 1, icon="📤"
        )
        self._create_kpi_card(
            kpi_row,
            "Success Rate",
            self.kpi_rate,
            "成功率",
            2,
            color=self.colors["success"],
            icon="✅",
        )
        self._create_kpi_card(
            kpi_row,
            "Est. Time",
            self.kpi_time,
            "剩余时间",
            3,
            color=self.colors["warning"],
            icon="⏳",
        )

        # --- 任务控制与状态 ---
        # 左右分栏
        content_row = tk.Frame(page, bg=self.colors["bg_app"])
        content_row.pack(fill=tk.BOTH, expand=True)

        # 左侧：任务状态 (占 2/3)
        left_col = tk.Frame(content_row, bg=self.colors["bg_app"])
        left_col.pack(
            side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, self.spacing["lg"])
        )

        task_card = self._create_card(left_col, "任务执行监控")
        task_card.pack(fill=tk.BOTH, expand=True)

        # 进度信息
        info_row = ttk.Frame(task_card, style="Card.TFrame")
        info_row.pack(fill=tk.X, pady=(0, 16))

        if not hasattr(self, "progress_var"):
            self.progress_var = tk.StringVar(value="0/0")

        # 进度数字
        ttk.Label(info_row, text="Current Progress", style="Secondary.TLabel").pack(
            side=tk.LEFT
        )
        ttk.Label(
            info_row,
            textvariable=self.progress_var,
            style="H2.TLabel",
            foreground=self.colors["primary"],
        ).pack(side=tk.RIGHT)

        # 进度条
        self.dash_progress = ttk.Progressbar(
            task_card,
            orient=tk.HORIZONTAL,
            mode="determinate",
            style="Horizontal.TProgressbar",
        )
        self.dash_progress.pack(fill=tk.X, pady=(0, 24))

        # 控制按钮组 (居左)
        ctrl_row = ttk.Frame(task_card, style="Card.TFrame")
        ctrl_row.pack(fill=tk.X)

        ttk.Button(
            ctrl_row,
            text="启动任务",
            style="Primary.TButton",
            command=self.start_sending,
        ).pack(side=tk.LEFT, padx=(0, 16))
        ttk.Button(
            ctrl_row,
            text="暂停",
            style="Secondary.TButton",
            command=self.toggle_pause_resume,
        ).pack(side=tk.LEFT, padx=(0, 16))
        ttk.Button(
            ctrl_row, text="终止", style="Danger.TButton", command=self.stop_sending
        ).pack(side=tk.LEFT)

        # 右侧：系统通知/日志概览 (占 1/3)
        right_col = tk.Frame(content_row, bg=self.colors["bg_app"], width=300)
        right_col.pack(side=tk.RIGHT, fill=tk.BOTH)

        log_card = self._create_card(right_col, "系统状态")
        log_card.pack(fill=tk.BOTH, expand=True)

        self.dash_status_label = ttk.Label(
            log_card, text="系统就绪", style="BadgeInfo.TLabel"
        )
        self.dash_status_label.pack(anchor="w", pady=(0, 16))

        return page

    def _init_header(self, parent):
        header = tk.Frame(parent, bg=self.colors["bg_app"])
        header.pack(fill=tk.X, pady=(0, self.spacing["xl"]))

        title_block = tk.Frame(header, bg=self.colors["bg_app"])
        title_block.pack(side=tk.LEFT)
        ttk.Label(
            title_block, text="WeChat Assistant Dashboard", style="H1.TLabel"
        ).pack(anchor="w")
        ttk.Label(
            title_block, text="微信自动化营销管理平台", style="SecondaryApp.TLabel"
        ).pack(anchor="w")

        status_block = tk.Frame(header, bg=self.colors["bg_app"])
        status_block.pack(side=tk.RIGHT, anchor="e")
        self.header_status_label = ttk.Label(
            status_block, text="系统就绪", style="BadgeInfo.TLabel"
        )
        self.header_status_label.pack(side=tk.RIGHT)

        if not hasattr(self, "status_label") or self.status_label is None:
            self.status_label = self.header_status_label

        return header

    def on_message_change(self, event=None):
        """当消息内容改变时保存"""
        content = self.message_text.get("1.0", "end-1c")
        if content != self.config.get("message_template"):
            self.config["message_template"] = content
            self.save_app_config()

    def build_kimi_rewrite_template(self, base_template):
        api_key = self.config["kimi_api_key"].get().strip()
        base_url = self.config["kimi_base_url"].get().strip()
        model = self.config["kimi_model"].get().strip()
        system_prompt = self.config["kimi_system_prompt"].get().strip()
        if not api_key or not base_url or not model:
            return base_template
        request_payload = {
            "model": model,
            "temperature": 0.9,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": f"请把下面模板改写成另一种表达方式，保留含义，保留占位符，不要解释，直接输出模板：\n{base_template}",
                },
            ],
        }
        try:
            data = json.dumps(request_payload, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                base_url,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = resp.read().decode("utf-8")
            result = json.loads(body)
            text = (
                result.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            )
            if not text:
                return base_template
            base_placeholders = sorted(
                re.findall(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}", base_template)
            )
            rewritten_placeholders = sorted(
                re.findall(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}", text)
            )
            if rewritten_placeholders != base_placeholders:
                self.update_log("⚠️ Kimi改写结果占位符与原模板不一致，已回退原模板")
                return base_template
            self.update_log("🤖 Kimi已生成新表达模板")
            self.update_log(f"🤖 Kimi返回文本: {text}")
            return text
        except Exception as e:
            self.update_log(f"⚠️ Kimi改写失败，已回退原模板: {e}")
            return base_template

    def get_active_message_template(self, contact_index):
        base_template = (
            self.message_text.get().strip() or self.config["message_template"]
        )
        if not self.config["kimi_rewrite_enabled"].get():
            self.kimi_current_template = base_template
            self.kimi_last_batch_index = -1
            return base_template
        batch_size = max(1, int(self.config["kimi_batch_size"].get()))
        batch_index = (contact_index - 1) // batch_size
        if batch_index == self.kimi_last_batch_index and self.kimi_current_template:
            return self.kimi_current_template
        rewritten = self.build_kimi_rewrite_template(base_template)
        self.kimi_current_template = rewritten
        self.kimi_last_batch_index = batch_index
        return rewritten

    def _fill_left_panel(self):
        """填充左侧配置区 - 现代化表单"""
        # 1. 发送配置卡片
        card_config = self._create_card(self.left_panel, "发送配置")

        # 图片选择
        ttk.Label(card_config, text="图片附件", style="Body.TLabel").pack(
            anchor="w", pady=(0, self.spacing["xs"])
        )

        img_row = ttk.Frame(card_config, style="Card.TFrame")
        img_row.pack(fill=tk.X, pady=(0, self.spacing["md"]))

        # 输入框容器 (模拟边框)
        entry_wrapper = tk.Frame(img_row, bg=self.colors["border"], padx=1, pady=1)
        entry_wrapper.pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, self.spacing["sm"])
        )

        self.image_path_entry = ttk.Entry(entry_wrapper, font=self.fonts["body"])
        self.image_path_entry.pack(fill=tk.BOTH, ipady=6, padx=8)  # 增加高度

        ttk.Button(
            img_row,
            text="浏览",
            style="Secondary.TButton",
            command=self.browse_image,
            width=8,
        ).pack(side=tk.LEFT)
        ttk.Button(
            img_row,
            text="上次",
            style="Secondary.TButton",
            command=self.use_last_images,
            width=8,
        ).pack(side=tk.LEFT, padx=(self.spacing["sm"], 0))

        # 预览区 (高度固定)
        self.preview_canvas = tk.Canvas(
            card_config,
            height=120,
            bg=self.colors["bg_app"],
            bd=0,
            highlightthickness=0,
        )
        self.preview_canvas.pack(fill=tk.X, pady=(0, self.spacing["md"]))
        self.preview_content = ttk.Frame(self.preview_canvas, style="App.TFrame")
        self.preview_canvas.create_window(
            (0, 0), window=self.preview_content, anchor="nw"
        )

        # 消息内容
        ttk.Label(card_config, text="消息内容", style="Body.TLabel").pack(
            anchor="w", pady=(0, self.spacing["xs"])
        )

        # 消息输入框容器 (模拟边框)
        msg_wrapper = tk.Frame(card_config, bg=self.colors["border"], padx=1, pady=1)
        msg_wrapper.pack(fill=tk.X, pady=(0, self.spacing["md"]))

        self.message_text = tk.Text(
            msg_wrapper,
            height=8,
            font=self.fonts["body"],
            bg="#FFFFFF",
            relief="flat",
            padx=12,
            pady=12,
        )
        self.message_text.pack(fill=tk.BOTH, expand=True)
        self.message_text.insert("1.0", self.config.get("message_template", ""))
        self.message_entry = self.message_text  # 兼容

        # 绑定内容修改事件
        self.message_text.bind("<KeyRelease>", self.on_message_change)

        # 核心修复：恢复 get_message 方法以兼容 Entry.get() 行为
        original_get = self.message_text.get

        def get_message(*args, **kwargs):
            if not args and not kwargs:
                return original_get("1.0", "end-1c")
            return original_get(*args, **kwargs)

        self.message_text.get = get_message

        # 选项组
        ttk.Label(card_config, text="发送选项", style="Body.TLabel").pack(
            anchor="w", pady=(0, self.spacing["xs"])
        )
        opts = ttk.Frame(card_config, style="Card.TFrame")
        opts.pack(fill=tk.X, pady=(0, self.spacing["md"]))

        # Radiobuttons 需要自定义 style 适配背景色
        self.style.configure(
            "Card.TRadiobutton",
            background=self.colors["bg_card"],
            font=self.fonts["body"],
        )
        self.style.configure(
            "Card.TCheckbutton",
            background=self.colors["bg_card"],
            font=self.fonts["body"],
        )

        ttk.Radiobutton(
            opts,
            text="先图后文",
            variable=self.config["send_order"],
            value="images_first",
            style="Card.TRadiobutton",
        ).pack(side=tk.LEFT, padx=(0, self.spacing["lg"]))
        ttk.Radiobutton(
            opts,
            text="先文后图",
            variable=self.config["send_order"],
            value="text_first",
            style="Card.TRadiobutton",
        ).pack(side=tk.LEFT)

        check_box = ttk.Frame(card_config, style="Card.TFrame")
        check_box.pack(fill=tk.X, pady=(self.spacing["sm"], 0))
        ttk.Checkbutton(
            check_box,
            text="从选中联系人开始",
            variable=self.config["send_from_selected"],
            style="Card.TCheckbutton",
        ).pack(anchor="w", pady=(0, 4))
        ttk.Checkbutton(
            check_box,
            text="跳过被筛选联系人",
            variable=self.config["skip_filtered_contacts"],
            style="Card.TCheckbutton",
        ).pack(anchor="w")

        # 1.5 安全设置卡片 (防风控)
        card_safety = self._create_card(self.left_panel, "安全设置")

        # 防风控模式开关
        safety_opts = ttk.Frame(card_safety, style="Card.TFrame")
        safety_opts.pack(fill=tk.X, pady=(0, self.spacing["md"]))

        ttk.Checkbutton(
            safety_opts,
            text="🛡️ 开启防风控模式 (推荐)",
            variable=self.config["anti_risk_mode"],
            style="Card.TCheckbutton",
        ).pack(anchor="w")
        ttk.Label(
            safety_opts,
            text="开启后将自动延长发送间隔并启用动态休息",
            style="Secondary.TLabel",
            font=self.fonts["small"],
        ).pack(anchor="w", padx=(24, 0))

        # 每日上限
        limit_row = ttk.Frame(card_safety, style="Card.TFrame")
        limit_row.pack(fill=tk.X, pady=(self.spacing["sm"], 0))

        ttk.Label(limit_row, text="每日发送上限:", style="Body.TLabel").pack(
            side=tk.LEFT
        )
        tk.Spinbox(
            limit_row,
            from_=1,
            to=5000,
            textvariable=self.config["daily_limit"],
            width=8,
        ).pack(side=tk.LEFT, padx=8)
        ttk.Label(limit_row, text="人", style="Body.TLabel").pack(side=tk.LEFT)

        card_kimi = self._create_card(self.left_panel, "AI改写设置")
        kimi_row_1 = ttk.Frame(card_kimi, style="Card.TFrame")
        kimi_row_1.pack(fill=tk.X, pady=(0, self.spacing["sm"]))
        ttk.Checkbutton(
            kimi_row_1,
            text="启用Kimi改写（每20人自动换表达）",
            variable=self.config["kimi_rewrite_enabled"],
            style="Card.TCheckbutton",
        ).pack(anchor="w")

        ttk.Label(card_kimi, text="Kimi API Key", style="Body.TLabel").pack(
            anchor="w", pady=(self.spacing["xs"], self.spacing["xs"])
        )
        kimi_key_entry = ttk.Entry(
            card_kimi,
            textvariable=self.config["kimi_api_key"],
            font=self.fonts["body"],
            show="*",
        )
        kimi_key_entry.pack(fill=tk.X, pady=(0, self.spacing["sm"]))

        ttk.Label(card_kimi, text="模型", style="Body.TLabel").pack(
            anchor="w", pady=(self.spacing["xs"], self.spacing["xs"])
        )
        kimi_model_entry = ttk.Entry(
            card_kimi, textvariable=self.config["kimi_model"], font=self.fonts["body"]
        )
        kimi_model_entry.pack(fill=tk.X, pady=(0, self.spacing["sm"]))

        kimi_row_2 = ttk.Frame(card_kimi, style="Card.TFrame")
        kimi_row_2.pack(fill=tk.X, pady=(0, self.spacing["sm"]))
        ttk.Label(kimi_row_2, text="每批人数", style="Body.TLabel").pack(side=tk.LEFT)
        tk.Spinbox(
            kimi_row_2,
            from_=5,
            to=100,
            textvariable=self.config["kimi_batch_size"],
            width=8,
        ).pack(side=tk.LEFT, padx=8)

        ttk.Label(
            card_kimi,
            text="提示词（保留占位符 {greeting} {name} {emoji}）",
            style="Secondary.TLabel",
            font=self.fonts["small"],
        ).pack(anchor="w", pady=(self.spacing["xs"], self.spacing["xs"]))
        kimi_prompt_entry = ttk.Entry(
            card_kimi,
            textvariable=self.config["kimi_system_prompt"],
            font=self.fonts["small"],
        )
        kimi_prompt_entry.pack(fill=tk.X)

        for key in [
            "kimi_rewrite_enabled",
            "kimi_api_key",
            "kimi_base_url",
            "kimi_model",
            "kimi_batch_size",
            "kimi_system_prompt",
        ]:
            self.config[key].trace_add("write", lambda *args: self.save_app_config())

        # 2. 核心操作卡片 (视觉重心)
        card_action = self._create_card(self.left_panel, "任务控制")

        # 进度数据
        data_row = ttk.Frame(card_action, style="Card.TFrame")
        data_row.pack(fill=tk.X, pady=(0, self.spacing["md"]))

        if not hasattr(self, "progress_var"):
            self.progress_var = tk.StringVar(value="0/0")
        lbl_progress = ttk.Label(
            data_row,
            textvariable=self.progress_var,
            font=self.fonts["h2"],
            foreground=self.colors["primary"],
            background=self.colors["bg_card"],
        )
        lbl_progress.pack(anchor="center")

        if not hasattr(self, "time_remaining_var"):
            self.time_remaining_var = tk.StringVar(value="预计剩余: --:--")
        ttk.Label(
            data_row,
            textvariable=self.time_remaining_var,
            style="Secondary.TLabel",
            font=("Segoe UI", 9),
        ).pack(anchor="center", pady=(4, 0))

        # 进度条
        self.config_progress = ttk.Progressbar(
            card_action,
            orient=tk.HORIZONTAL,
            length=100,
            mode="determinate",
            style="Horizontal.TProgressbar",
        )
        self.config_progress.pack(fill=tk.X, pady=(0, self.spacing["lg"]))
        self.progress_bar = self.config_progress

        # 主按钮 (加大)
        self.start_button = ttk.Button(
            card_action,
            text="🚀 开始发送任务",
            style="Primary.TButton",
            command=self.start_sending,
        )
        self.start_button.pack(fill=tk.X, pady=(0, self.spacing["md"]), ipady=6)

        # 辅助按钮组
        ctrl_row = ttk.Frame(card_action, style="Card.TFrame")
        ctrl_row.pack(fill=tk.X)

        self.pause_button = ttk.Button(
            ctrl_row,
            text="暂停",
            style="Secondary.TButton",
            command=self.toggle_pause_resume,
            state=tk.DISABLED,
        )
        self.pause_button.pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, self.spacing["sm"])
        )

        ttk.Button(
            ctrl_row, text="停止", style="Danger.TButton", command=self.stop_sending
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(self.spacing["sm"], 0))

    def _fill_right_panel(self):
        """填充右侧数据区"""
        # 使用 Notebook 分页
        self.notebook = ttk.Notebook(self.right_panel)
        self.notebook.pack(fill=tk.BOTH, expand=True, pady=(0, self.spacing["lg"]))

        # Tab 1: 联系人管理
        tab_contacts = ttk.Frame(
            self.notebook, style="App.TFrame"
        )  # 注意这里背景色要处理
        self.notebook.add(tab_contacts, text="  联系人列表  ")
        self._init_contacts_tab(tab_contacts)

        # Tab 2: 运行日志
        tab_logs = ttk.Frame(self.notebook, style="App.TFrame")
        self.notebook.add(tab_logs, text="  运行日志  ")
        self._init_logs_tab(tab_logs)

    def _init_contacts_tab(self, parent):
        """
        联系人管理 - 结构级 UI 重构
        目标: 提升层级控制力、降低信息噪音、增强专业 SaaS 气质
        """
        # --- 1. Header (视觉层级强化) ---
        header = tk.Frame(parent, bg=self.colors["bg_app"])
        header.pack(fill=tk.X, pady=(0, self.spacing["xl"]))  # 增加底部间距

        title_box = tk.Frame(header, bg=self.colors["bg_app"])
        title_box.pack(side=tk.LEFT)
        ttk.Label(title_box, text="Contacts", style="H1.TLabel").pack(
            anchor="w"
        )  # 英文标题提升专业感
        ttk.Label(
            title_box, text="联系人数据库与筛选", style="SecondaryApp.TLabel"
        ).pack(anchor="w", pady=(4, 0))

        # --- 2. 顶部操作区 (结构优化: 拆分为 过滤 / 主操作 / 辅助) ---
        # 使用 grid 布局来更好地控制三个区块的分布
        toolbar = tk.Frame(parent, bg=self.colors["bg_app"])
        toolbar.pack(fill=tk.X, pady=(0, self.spacing["lg"]))

        # A. 过滤区 (左侧)
        filter_section = tk.Frame(toolbar, bg=self.colors["bg_app"])
        filter_section.pack(side=tk.LEFT)

        # 搜索框 (增加宽度和内边距)
        search_frame = tk.Frame(
            filter_section, bg=self.colors["border"], padx=1, pady=1
        )  # 模拟边框
        search_frame.pack(side=tk.LEFT, padx=(0, self.spacing["md"]))

        self.search_entry = ttk.Entry(search_frame, font=self.fonts["body"], width=35)
        self.search_entry.pack(fill=tk.BOTH, ipady=4, padx=8)  # 增加内部高度
        self.search_entry.bind("<KeyRelease>", self.search_contacts)

        # 标签筛选
        self.tag_var = tk.StringVar(value="所有标签")
        self.tag_combobox = ttk.Combobox(
            filter_section,
            textvariable=self.tag_var,
            width=15,
            state="readonly",
            font=self.fonts["body"],
        )
        self.tag_combobox.pack(side=tk.LEFT, padx=(0, self.spacing["sm"]), ipady=4)
        self.tag_combobox.bind(
            "<<ComboboxSelected>>", lambda e: self.filter_contacts_by_tag()
        )

        # 清除按钮 (弱化)
        ttk.Button(
            filter_section,
            text="重置",
            style="Secondary.TButton",
            command=self.clear_tag_filter,
        ).pack(side=tk.LEFT)

        # C. 辅助工具区 (右侧 - 弱化处理)
        aux_section = tk.Frame(toolbar, bg=self.colors["bg_app"])
        aux_section.pack(side=tk.RIGHT)

        # 更多操作菜单 (模拟)
        ttk.Button(
            aux_section,
            text="📥 导入",
            style="Secondary.TButton",
            command=self.import_contacts_from_txt,
        ).pack(side=tk.LEFT, padx=(0, self.spacing["sm"]))
        ttk.Button(
            aux_section,
            text="📤 导出",
            style="Secondary.TButton",
            command=self.export_contacts_to_txt,
        ).pack(side=tk.LEFT)

        # B. 主操作区 (中间 - 仅保留核心刷新)
        # 这里实际上为了布局平衡，刷新按钮可以放在过滤区旁边，或者单独放
        ttk.Button(
            filter_section,
            text="🔄 刷新列表",
            style="Primary.TButton",
            command=self.refresh_contacts_threaded,
        ).pack(side=tk.LEFT, padx=(self.spacing["md"], 0))

        # --- 3. 列表区域 (视觉层级强化 + 减法原则) ---
        # 移除卡片外边框，直接使用背景色区分
        list_container = tk.Frame(parent, bg=self.colors["bg_card"])  # 白色背景容器
        list_container.pack(fill=tk.BOTH, expand=True)

        # 顶部工具栏 (全选/反选 - 移至表格上方，更符合操作逻辑)
        list_actions = tk.Frame(
            list_container, bg=self.colors["bg_card"], padx=24, pady=16
        )
        list_actions.pack(fill=tk.X)

        ttk.Button(
            list_actions,
            text="全选",
            style="Secondary.TButton",
            command=self.select_all_contacts,
            width=8,
        ).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(
            list_actions,
            text="反选",
            style="Secondary.TButton",
            command=self.deselect_all_contacts,
            width=8,
        ).pack(side=tk.LEFT)

        # 将导出按钮也放在列表上方，方便操作
        ttk.Button(
            list_actions,
            text="📤 导出列表",
            style="Secondary.TButton",
            command=self.export_contacts_to_txt,
        ).pack(side=tk.LEFT, padx=(8, 0))

        # 统计信息
        self.contact_count_label = ttk.Label(
            list_actions, text="共 0 位联系人", style="Secondary.TLabel"
        )
        self.contact_count_label.pack(side=tk.RIGHT)

        # 表格容器
        tree_frame = tk.Frame(list_container, bg=self.colors["bg_card"])
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=24, pady=(0, 24))

        # Treeview (视觉优化: 浅灰表头，去除边框，增加行高)
        columns = ("name", "remark", "tags", "status")
        self.contact_tree = ttk.Treeview(
            tree_frame,
            columns=columns,
            show="headings",
            selectmode="extended",
            style="Treeview",
        )

        # 表头配置
        self.contact_tree.heading("name", text="微信昵称", anchor="w")
        self.contact_tree.heading("remark", text="备注名", anchor="w")
        self.contact_tree.heading("tags", text="标签", anchor="center")
        self.contact_tree.heading("status", text="状态", anchor="center")

        self.contact_tree.column("name", width=200, anchor="w")
        self.contact_tree.column("remark", width=200, anchor="w")
        self.contact_tree.column("tags", width=120, anchor="center")
        self.contact_tree.column("status", width=100, anchor="center")

        # 滚动条 (贴合右侧)
        scrollbar = tk.Scrollbar(
            tree_frame,
            orient="vertical",
            command=self.contact_tree.yview,
            bg=self.colors["bg_app"],
            relief="flat",
            width=12,
        )
        self.contact_tree.configure(yscrollcommand=scrollbar.set)

        self.contact_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 绑定选中事件以更新统计
        self.contact_tree.bind(
            "<<TreeviewSelect>>", self._on_contact_select_update_info
        )

    def _on_contact_select_update_info(self, event):
        """更新选中统计信息"""
        selection = self.contact_tree.selection()
        total = len(self.contact_tree.get_children())
        self.contact_count_label.config(
            text=f"已选 {len(selection)} / 共 {total} 位联系人"
        )

    def _create_rules_view(self):
        """创建规则管理页面"""
        # 确保页面已定义
        page = tk.Frame(self.page_container, bg=self.colors["bg_app"])

        # Header
        header = tk.Frame(page, bg=self.colors["bg_app"])
        header.pack(fill=tk.X, pady=(0, 24))

        title_block = tk.Frame(header, bg=self.colors["bg_app"])
        title_block.pack(side=tk.LEFT)
        ttk.Label(title_block, text="Rules", style="H1.TLabel").pack(anchor="w")
        ttk.Label(
            title_block, text="自动处理规则与关键词配置", style="SecondaryApp.TLabel"
        ).pack(anchor="w", pady=(4, 0))

        # 规则配置卡片
        container = tk.Frame(page, bg=self.colors["bg_app"])
        container.pack(fill=tk.BOTH, expand=True)

        # 使用 Notebook 组织规则分类
        self.rule_notebook = ttk.Notebook(container)
        self.rule_notebook.pack(fill=tk.BOTH, expand=True)

        categories = [
            ("过滤关键词", "filter_keywords", "过滤包含这些词的联系人（不发送）"),
            (
                "内容清洗",
                "clean_keywords",
                "内容清洗：发送前会将名字中的这些词**删除**，但**不会跳过**该联系人。（例如：把'张三(云山)'清洗为'张三'）",
            ),
            (
                "称谓去除",
                "title_keywords",
                "称谓去除：智能识别并去除名字中的称谓，只保留核心名字。（例如：'李明爸爸' -> '李明'，'王老师' -> '王'）",
            ),
            (
                "年级去除",
                "grade_keywords",
                "年级去除：去除名字中包含的年级信息，避免称呼中带上班级。（例如：'初一张三' -> '张三'）",
            ),
            (
                "干扰词库",
                "noise_keywords",
                "机构去噪：去除名字中混入的公司、机构、职业等干扰词。（例如：'张三(汇通金融)' -> '张三'）",
            ),
            (
                "复姓库",
                "compound_surnames",
                "复姓识别：用于正确识别复姓（如欧阳、司马），确保截取名字时不会把姓氏截断。",
            ),
            (
                "删除检测",
                "delete_keywords",
                "被删检测：程序通过检测聊天记录或错误信息中是否包含这些词，来判断是否被对方删除或拉黑。（例如：'开启了朋友验证'）",
            ),
            (
                "无效关键词",
                "invalid_keywords",
                "无效名判定(包含)：如果清洗后的名字**包含**这些词，将被视为无效名字，发送时会使用通用称呼（如'朋友'）。（例如：'销售', '客服'）",
            ),
            (
                "精准无效名",
                "exact_match_invalid_names",
                "无效名判定(完全匹配)：只有当清洗后的名字**完全等于**这些词时，才视为无效名字。（例如：名字只剩'初一'或'高三'时，视为无效）",
            ),
            (
                "过滤字符",
                "filter_chars",
                "字符剔除：强制删除名字中的特定单个字符。（例如：删除名字里的'春','夏'等季节词）",
            ),
            (
                "正则过滤",
                "filter_patterns",
                "正则清洗：使用正则表达式进行高级清洗。（例如：`初[0-9]+` 可以去除'初一'、'初12'等所有变体）",
            ),
        ]

        self.rule_text_widgets = {}

        for title, key, desc in categories:
            self._create_rule_tab(self.rule_notebook, title, key, desc)

        return page

    def _create_rule_tab(self, notebook, title, key, desc):
        """创建单个规则 Tab"""
        frame = ttk.Frame(
            notebook, style="Card.TFrame", padding=self.spacing["lg"]
        )  # 卡片背景
        notebook.add(frame, text=f"  {title}  ")

        # 说明
        ttk.Label(frame, text=desc, style="Secondary.TLabel").pack(
            anchor="w", pady=(0, 12)
        )

        # 文本编辑区
        text_frame = tk.Frame(frame, bg=self.colors["border"], padx=1, pady=1)  # 边框
        text_frame.pack(fill=tk.BOTH, expand=True)

        text_area = scrolledtext.ScrolledText(
            text_frame,
            width=60,
            height=15,
            font=("Consolas", 10),
            relief="flat",
            padx=12,
            pady=12,
        )
        text_area.pack(fill=tk.BOTH, expand=True)

        # 加载数据
        current_rules = self.config.get(key, [])
        text_area.insert(tk.END, "\n".join(current_rules))

        self.rule_text_widgets[key] = text_area

        # 保存按钮
        btn_frame = ttk.Frame(frame, style="Card.TFrame")
        btn_frame.pack(fill=tk.X, pady=(16, 0))
        ttk.Button(
            btn_frame,
            text="💾 保存规则",
            style="Primary.TButton",
            command=lambda: self.save_rule(key),
        ).pack(side=tk.RIGHT)

    def _init_logs_tab(self, parent):
        """日志 Tab 内容"""
        # Header
        header = tk.Frame(parent, bg=self.colors["bg_app"])
        header.pack(fill=tk.X, pady=(0, 24))
        ttk.Label(header, text="系统运行日志", style="H1.TLabel").pack(anchor="w")

        log_card = self._create_card(parent)
        log_card.pack_configure(fill=tk.BOTH, expand=True)

        self.log_text = scrolledtext.ScrolledText(
            log_card,
            font=("Consolas", 10),
            bg="#1F2937",
            fg="#F9FAFB",  # 深色模式日志
            relief="flat",
            padx=16,
            pady=16,
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.config(state=tk.DISABLED)

    def bind_events(self):
        """绑定所有事件"""
        # 键盘快捷键
        self.root.bind("<p>", self.on_hotkey_press)
        self.root.bind("<P>", self.on_hotkey_press)
        self.root.bind("<End>", self.on_hotkey_press)
        self.root.bind("<space>", lambda e: self.toggle_pause_resume())
        self.root.bind("<Configure>", self.on_window_resize)

        self.root.focus_set()
        self.update_log("✅ 初始化完成 - 所有功能已就绪")

    def on_window_resize(self, event):
        if event.widget != self.root:
            return
        current_width = int(getattr(event, "width", 0) or 0)
        if current_width <= 0:
            return
        last_width = getattr(self, "last_width", 0)
        if last_width and abs(last_width - current_width) < 10:
            return
        self.last_width = current_width
        self.update_fonts()
        if current_width <= 1200:
            if getattr(self, "layout_mode", "") != "compact":
                self.layout_mode = "compact"
                self.root.after(50, self.switch_to_compact_layout)
        else:
            if getattr(self, "layout_mode", "") != "normal":
                self.layout_mode = "normal"
                self.root.after(50, self.switch_to_normal_layout)

    def switch_to_compact_layout(self):
        sidebar = getattr(self, "sidebar_frame", None)
        if sidebar and sidebar.winfo_exists():
            sidebar.config(width=220)
        page_container = getattr(self, "page_container", None)
        if page_container and page_container.winfo_exists():
            page_container.pack_configure(
                padx=self.spacing.get("lg", 24), pady=self.spacing.get("lg", 24)
            )
        for widgets in getattr(self, "nav_buttons", {}).values():
            label = widgets.get("label")
            icon = widgets.get("icon")
            if label and label.winfo_exists():
                label.configure(font=("Segoe UI", 9))
            if icon and icon.winfo_exists():
                icon.configure(font=("Segoe UI", 12))
        self.show_page(getattr(self, "current_page_id", "dashboard") or "dashboard")

    def switch_to_normal_layout(self):
        sidebar = getattr(self, "sidebar_frame", None)
        if sidebar and sidebar.winfo_exists():
            sidebar.config(width=260)
        page_container = getattr(self, "page_container", None)
        if page_container and page_container.winfo_exists():
            page_container.pack_configure(
                padx=self.spacing.get("xl", 32), pady=self.spacing.get("xl", 32)
            )
        for pid, widgets in getattr(self, "nav_buttons", {}).items():
            label = widgets.get("label")
            icon = widgets.get("icon")
            if icon and icon.winfo_exists():
                icon.configure(font=("Segoe UI", 14))
            if label and label.winfo_exists():
                if pid == getattr(self, "current_page_id", None):
                    label.configure(font=self.fonts["nav_active"])
                else:
                    label.configure(font=self.fonts["nav"])
        self.show_page(getattr(self, "current_page_id", "dashboard") or "dashboard")

    def update_fonts(self):
        try:
            current_width = self.root.winfo_width()
            if current_width < 100:
                return
            base_width = max(int(getattr(self, "initial_window_width", 700)), 700)
            scale = current_width / base_width
            new_font_size = int(self.base_font_size * scale)
            new_font_size = max(
                self.min_font_size, min(self.max_font_size, new_font_size)
            )
            if new_font_size == self.current_font_size:
                return
            self.current_font_size = new_font_size
            self.font_scale_factor = new_font_size / float(self.base_font_size)
            self.apply_fonts_to_widgets()
        except Exception:
            return

    def apply_fonts_to_widgets(self):
        for widget_info in list(getattr(self, "all_font_widgets", [])):
            widget = widget_info.get("widget")
            base_size = int(widget_info.get("base_size", self.base_font_size))
            weight = widget_info.get("weight", "normal")
            if not widget:
                continue
            try:
                if not widget.winfo_exists():
                    continue
                scaled = int(
                    base_size * self.current_font_size / max(self.base_font_size, 1)
                )
                scaled = max(self.min_font_size, min(self.max_font_size, scaled))
                if weight == "normal":
                    widget.config(font=("Segoe UI", scaled))
                else:
                    widget.config(font=("Segoe UI", scaled, weight))
            except Exception:
                continue

    def register_font_widget(self, widget, base_size, weight="normal"):
        if widget is None:
            return
        for item in self.all_font_widgets:
            if item.get("widget") is widget:
                item["base_size"] = base_size
                item["weight"] = weight
                return
        self.all_font_widgets.append(
            {"widget": widget, "base_size": base_size, "weight": weight}
        )

    def init_wechat(self):
        """初始化微信"""
        try:
            import importlib.metadata

            # 获取wxauto版本
            try:
                wxauto_version = importlib.metadata.version("wxauto")
                self.update_log(f"📦 wxauto版本: {wxauto_version}")
            except Exception:
                wxauto_version = "未知版本"

            # 使用单例获取 WeChat 实例（内置前置检查和 COM 初始化）
            self.wx = get_wechat()
            self.is_initialized = True
            self.update_status(
                f"✅ 微信客户端初始化成功！(wxauto版本: {wxauto_version})",
                "#52C41A",
            )
            self.update_log("✅ 微信已连接 - 点击【刷新联系人】获取列表")
        except ImportError:
            self.is_initialized = False
            self.update_status("❌ 未安装wxauto库", "#FF4D4F")
            self.update_log("❌ 请安装wxauto: pip install wxauto")
        except RuntimeError as e:
            self.is_initialized = False
            self.update_status(f"❌ 微信未就绪: {str(e)[:50]}...", "#FF4D4F")
            self.update_log(f"❌ {e}")
        except Exception as e:
            self.is_initialized = False
            error_msg = str(e)
            self.update_status(f"❌ 微信初始化失败: {error_msg[:50]}...", "#FF4D4F")
            self.update_log(f"❌ 微信初始化失败: {error_msg}")

            # 提供更详细的错误信息和解决方案
            if "未找到微信窗口" in error_msg:
                self.update_log(
                    "💡 解决方案: 请确保微信PC端已登录且处于前台，或尝试重启微信"
                )
            elif "4.0" in error_msg:
                self.update_log("💡 解决方案: 请下载3.9版本微信客户端使用本项目")
            elif "control" in error_msg or "CoInitialize" in error_msg:
                self.update_log(
                    "💡 解决方案: 请尝试：\n"
                    "   ① 确保微信已完全登录（非登录页面）\n"
                    "   ② 点击系统托盘微信图标恢复微信窗口\n"
                    "   ③ 关闭并重新启动微信"
                )

    def update_log(self, msg):
        """更新日志（线程安全版）"""
        # 如果当前不是主线程，则调度到主线程执行
        if threading.current_thread() is threading.main_thread():
            self._update_log_impl(msg)
        else:
            self.run_in_main_thread(self._update_log_impl, msg)

    def show_toast(self, message, type="info"):
        """显示 Toast 通知 (模拟)"""
        # 在主窗口中央显示一个短暂的浮层
        try:
            toast = tk.Toplevel(self.root)
            toast.overrideredirect(True)  # 无边框
            toast.attributes("-topmost", True)

            bg_color = "#333333"
            fg_color = "#FFFFFF"
            if type == "success":
                bg_color = self.colors["success"]
            elif type == "error":
                bg_color = self.colors["danger"]
            elif type == "warning":
                bg_color = self.colors["warning"]
                fg_color = "#000000"

            toast.configure(bg=bg_color)

            # 使用 ttk.Label 可能会受到 theme 影响，使用 tk.Label 更稳
            label = tk.Label(
                toast,
                text=message,
                font=self.fonts["body"],
                bg=bg_color,
                fg=fg_color,
                padx=20,
                pady=10,
            )
            label.pack()

            # 居中定位
            self.root.update_idletasks()
            # 获取根窗口位置，注意可能为负数（多屏）
            root_x = self.root.winfo_x()
            root_y = self.root.winfo_y()
            root_w = self.root.winfo_width()
            root_h = self.root.winfo_height()

            toast_w = label.winfo_reqwidth()
            toast_h = label.winfo_reqheight()

            x = root_x + (root_w - toast_w) // 2
            y = root_y + (root_h - toast_h) // 2

            toast.geometry(f"+{x}+{y}")

            # 2秒后自动消失
            self.root.after(2000, toast.destroy)
        except Exception:
            pass  # 忽略 toast 错误

    def _update_log_impl(self, msg):
        """实际更新日志的实现"""
        if not hasattr(self, "log_text") or not self.log_text:
            # 如果日志组件还没创建，打印到控制台即可
            logger.info(f"[UI Log] {msg}")
            return

        try:
            self.log_text.config(state=tk.NORMAL)
            timestamp = datetime.now().strftime("%H:%M:%S")
            log_line = f"[{timestamp}] {msg}\n"

            # 插入文本
            self.log_text.insert(tk.END, log_line)

            # 简单的颜色处理 (可选，根据关键词)
            if "❌" in msg or "Error" in msg:
                self.log_text.tag_add("error", "end-2l", "end-1l")
                self.log_text.tag_config("error", foreground="#EF4444")
            elif "✅" in msg or "成功" in msg:
                self.log_text.tag_add("success", "end-2l", "end-1l")
                self.log_text.tag_config("success", foreground="#10B981")

            # 自动滚动到底部
            self.log_text.see(tk.END)
            self.log_text.config(state=tk.DISABLED)
        except Exception:
            # 防止UI已销毁时报错
            pass

        logger.info(msg)

    def update_preview(self):
        """更新图片预览显示"""
        # 清空现有预览内容
        for widget in self.preview_content.winfo_children():
            widget.destroy()

        if not self.image_paths:
            # 显示占位符
            self.preview_placeholder = ttk.Label(
                self.preview_content, text="未选择图片", style="Secondary.TLabel"
            )
            self.preview_placeholder.pack(pady=20)
            return

        # 显示所有图片预览
        for idx, path in enumerate(self.image_paths):
            img_frame = ttk.Frame(self.preview_content, style="Card.TFrame", padding=5)
            img_frame.pack(side="left", padx=5, pady=5)

            # 显示图片序号
            idx_label = ttk.Label(img_frame, text=f"#{idx + 1}", style="Badge.TLabel")
            idx_label.pack()

            # 显示图片名
            img_name = os.path.basename(path)
            # 截断过长的文件名
            if len(img_name) > 10:
                img_name = img_name[:8] + "..."
            name_label = ttk.Label(img_frame, text=img_name, style="Body.TLabel")
            name_label.pack()

            # 排序按钮框架
            sort_frame = ttk.Frame(img_frame, style="Card.TFrame")
            sort_frame.pack(pady=3)

            # 上移按钮
            up_btn = ttk.Button(
                sort_frame,
                text="↑",
                command=lambda i=idx: self.move_image_up(i),
                width=2,
            )
            up_btn.pack(side=tk.LEFT, padx=1)
            up_btn.config(state=tk.NORMAL if idx > 0 else tk.DISABLED)

            # 下移按钮
            down_btn = ttk.Button(
                sort_frame,
                text="↓",
                command=lambda i=idx: self.move_image_down(i),
                width=2,
            )
            down_btn.pack(side=tk.LEFT, padx=1)
            down_btn.config(
                state=tk.NORMAL if idx < len(self.image_paths) - 1 else tk.DISABLED
            )

            # 删除按钮
            delete_btn = ttk.Button(
                img_frame,
                text="×",
                command=lambda i=idx: self.remove_image(i),
                width=3,
                style="Danger.TButton",
            )
            delete_btn.pack(pady=2)

    def move_image_up(self, index):
        """将图片上移一位"""
        if 0 < index < len(self.image_paths):
            # 交换位置
            self.image_paths[index], self.image_paths[index - 1] = (
                self.image_paths[index - 1],
                self.image_paths[index],
            )
            # 更新预览
            self.update_preview()
            # 更新输入框显示
            if self.image_paths:
                display_text = f"已选择 {len(self.image_paths)} 张图片：{os.path.basename(self.image_paths[0])}"
                if len(self.image_paths) > 1:
                    display_text += " 等"
                self.image_path_entry.delete(0, tk.END)
                self.image_path_entry.insert(0, display_text)

    def move_image_down(self, index):
        """将图片下移一位"""
        if 0 <= index < len(self.image_paths) - 1:
            # 交换位置
            self.image_paths[index], self.image_paths[index + 1] = (
                self.image_paths[index + 1],
                self.image_paths[index],
            )
            # 更新预览
            self.update_preview()
            # 更新输入框显示
            if self.image_paths:
                display_text = f"已选择 {len(self.image_paths)} 张图片：{os.path.basename(self.image_paths[0])}"
                if len(self.image_paths) > 1:
                    display_text += " 等"
                self.image_path_entry.delete(0, tk.END)
                self.image_path_entry.insert(0, display_text)

    def remove_image(self, index):
        """删除指定索引的图片"""
        if 0 <= index < len(self.image_paths):
            removed = self.image_paths.pop(index)
            self.update_log(f"🗑️  删除图片: {os.path.basename(removed)}")
            # 更新输入框显示
            if self.image_paths:
                display_text = f"已选择 {len(self.image_paths)} 张图片：{os.path.basename(self.image_paths[0])}"
                if len(self.image_paths) > 1:
                    display_text += " 等"
                self.image_path_entry.delete(0, tk.END)
                self.image_path_entry.insert(0, display_text)
            else:
                self.image_path_entry.delete(0, tk.END)
            # 更新预览
            self.update_preview()

    def browse_image(self):
        """选择多张图片"""
        try:
            # 优先使用上次的目录
            initial_dir = self.last_image_dir if self.last_image_dir else None

            paths = filedialog.askopenfilenames(
                title="选择图片",
                filetypes=[
                    ("图片文件", "*.jpg *.jpeg *.png *.gif *.bmp"),
                    ("所有文件", "*.*"),
                ],
                initialdir=initial_dir,
            )
            if paths:
                self.image_paths = list(paths)

                # 记录本次打开的目录，供下次使用
                if self.image_paths:
                    self.last_image_dir = os.path.dirname(self.image_paths[0])
                    # 保存配置到文件
                    self.save_last_session_config()

                # 更新输入框显示，显示图片数量和第一个图片名
                if self.image_paths:
                    display_text = f"已选择 {len(self.image_paths)} 张图片：{os.path.basename(self.image_paths[0])}"
                    if len(self.image_paths) > 1:
                        display_text += " 等"
                    self.image_path_entry.delete(0, tk.END)
                    self.image_path_entry.insert(0, display_text)
                else:
                    self.image_path_entry.delete(0, tk.END)
                # 更新日志
                for path in self.image_paths:
                    self.update_log(f"📷 已选择图片: {os.path.basename(path)}")
                # 更新预览
                self.update_preview()
        except Exception as e:
            self.update_log(f"❌ 图片选择失败: {str(e)}")
            messagebox.showerror("错误", f"图片选择失败: {e}")

    def filter_final_text(self, text):
        """过滤文本"""
        if not text:
            return text
        filtered = text
        for char in self.config["filter_chars"]:
            filtered = filtered.replace(char, "")
        for pattern in self.config["filter_patterns"]:
            filtered = re.sub(pattern, "", filtered)
        filtered = re.sub(r"\s+", "", filtered)
        return filtered if filtered else "朋友"

    def extract_pure_name(self, full_name):
        """提取纯姓名（优化版：支持复姓、智能去干扰、强制去姓）"""
        if not full_name:
            return ""
        name = full_name.strip()

        # 0. 强过滤关键词处理（使用 config 中的 content_cleaning_keywords）
        # 如果包含这些关键词，提取名字时会把它们去掉，避免出现在称呼中
        for kw in self.config.get("clean_keywords", []):
            name = name.replace(kw, "")

        # 0.1 过滤数字关键词（仅当数字单独出现或与特定词组合时才过滤，避免误伤名字中的数字）
        # 这里移除单独的数字过滤循环，依靠后续的正则和关键词处理

        original_name = name  # 保存原始名称

        # 1. 基础清理：移除电话号码、冒号、括号
        name = re.sub(r"\d{11}", "", name)
        if ":" in name:
            name = name.split(":")[0]
        if "：" in name:  # 支持中文冒号
            name = name.split("：")[0]
        name = re.sub(r"\(.*?\)|（.*?）", "", name)

        # 2. 移除干扰词（使用替换而非切割，防止误伤）
        # 使用 config 中的 noise_keywords
        for keyword in self.config.get("noise_keywords", []):
            name = name.replace(keyword, "")

        # 移除@符号
        name = name.replace("@", "")

        # 3. 移除称谓（使用 config 中的 title_keywords）
        for term in self.config.get("title_keywords", []):
            name = name.replace(term, "")

        # 4. 处理"职业-姓名"格式（如：XX公司-王小明 -> 王小明）
        if "-" in name:
            parts = name.split("-")
            # 取最后一部分，通常是名字
            name = parts[-1].strip()

        # 5. 处理年级/班级信息
        # 移除常见的年级词汇（使用 config 中的 grade_keywords）
        for term in self.config.get("grade_keywords", []):
            name = name.replace(term, "")

        # 移除数字开头的年级/班级模式 (如 "1班", "201级")
        name = re.sub(r"^[0-9一二三四五六七八九十]+(?:班|届|级)?", "", name)
        # 移除以"初/高/小/升"开头的年级
        name = re.sub(r"^(?:初|高|小|升)[0-9一二三四五六七八九十]+", "", name)

        # 6. 过滤文本中的特殊字符
        name = self.filter_final_text(name)

        # 7. 提取中文名并处理去姓逻辑
        # 提取所有中文字符
        chinese_chars = re.findall(r"[\u4e00-\u9fa5]", name)
        final_name = "".join(chinese_chars)

        # 如果提取结果为空或太短，尝试从原始名称兜底提取
        if len(final_name) < 2:
            # 兜底：从原始名称中提取所有汉字，去掉已知干扰词
            temp_original = original_name
            all_noise = (
                self.config.get("noise_keywords", [])
                + self.config.get("title_keywords", [])
                + self.config.get("grade_keywords", [])
            )
            for kw in all_noise:
                temp_original = temp_original.replace(kw, "")
            chinese_chars = re.findall(r"[\u4e00-\u9fa5]", temp_original)
            final_name = "".join(chinese_chars)

        # ========== 核心：智能去姓逻辑 ==========
        if len(final_name) >= 2:
            # 常见复姓列表（使用 config 中的 compound_surnames）
            is_compound = False
            for surname in self.config.get("compound_surnames", []):
                if final_name.startswith(surname):
                    is_compound = True
                    # 复姓：如果名字长度 > 2，则去掉复姓（前2字）
                    # 例如：欧阳修 (3) -> 修；欧阳娜娜 (4) -> 娜娜
                    if len(final_name) > 2:
                        final_name = final_name[2:]
                    break

            if not is_compound:
                # 单姓：如果名字长度 >= 3，则去掉姓（前1字）
                # 例如：王小明 (3) -> 小明；张伟 (2) -> 张伟（保持原样）
                if len(final_name) >= 3:
                    final_name = final_name[1:]

        return final_name if final_name else "朋友"

    def refresh_contacts_threaded(self):
        """线程刷新联系人"""
        if self.is_refreshing:
            messagebox.showwarning("提示", "正在刷新联系人，请稍候...")
            return
        if not self.is_initialized:
            messagebox.showerror("错误", "微信客户端尚未初始化！")
            return
        self.is_refreshing = True
        threading.Thread(target=self.refresh_contacts, daemon=True).start()

    def refresh_contacts(self):
        """刷新联系人 - 优化GetFriendDetails调用"""
        try:
            self.update_status("🔄 正在获取联系人列表...", "#5B8FF9")
            self.update_log("🔄 开始读取微信联系人...")

            # 检查微信是否已初始化
            if not self.is_initialized or not self.wx:
                raise Exception("微信客户端未初始化")

            # 尝试获取联系人列表（兼容不同wxauto版本）
            all_friends = []
            try:
                all_friends = self.wx.GetFriendDetails(
                    timeout=20000
                )  # 延长超时确保获取完整
            except AttributeError:
                # 兼容旧版wxauto
                try:
                    all_friends = self.wx.GetFriends()
                except AttributeError:
                    # 兼容更旧版wxauto
                    all_friends = self.wx.GetAllContacts()

            # 验证获取结果
            if not isinstance(all_friends, list):
                raise Exception(
                    f"获取到的联系人数据类型错误: {type(all_friends).__name__}"
                )

            self.update_log(f"📥 从微信获取到 {len(all_friends)} 条联系人数据")

            self.contacts = []
            seen_contacts = set()  # 用于去重
            filtered_count = 0
            extracted_count = 0

            for friend in all_friends:
                if isinstance(friend, dict):
                    # 适配不同wxauto版本的字段名（兼容新旧版本）
                    nickname = (
                        friend.get("昵称", "")
                        or friend.get("nickname", "")
                        or friend.get("NickName", "")
                        or ""
                    )
                    remark = (
                        friend.get("备注", "")
                        or friend.get("remark", "")
                        or friend.get("RemarkName", "")
                        or ""
                    )
                    wxid = (
                        friend.get("微信号", "")
                        or friend.get("alias", "")
                        or friend.get("Alias", "")
                        or ""
                    )

                    # 生成唯一键进行去重 (优先使用微信号，如果没有则使用昵称+备注)
                    # 注意：wxauto可能不返回微信号，所以使用 昵称+备注 作为兜底
                    unique_key = (nickname, remark, wxid)
                    if unique_key in seen_contacts:
                        continue
                    seen_contacts.add(unique_key)

                    # 提取标签信息
                    tags = friend.get("标签", "")

                    # 优先用备注，没有备注用昵称
                    display_name = remark if remark else nickname

                    # 1. 硬黑名单过滤：如果包含 hard_block_keywords 中的任何一个，直接跳过不显示
                    is_hard_blocked = any(
                        kw in display_name or kw in remark or kw in nickname
                        for kw in self.config.get("hard_block_keywords", [])
                    )
                    if is_hard_blocked:
                        continue

                    # 2. 软筛选过滤：检查是否包含过滤关键词，但不跳过，仅标记为 filtered
                    is_filtered = any(
                        kw in display_name or kw in remark or kw in nickname
                        for kw in self.config["filter_keywords"]
                    )
                    if is_filtered:
                        filtered_count += 1

                    # 智能提取纯名字（保留你当前的提取逻辑）
                    pure_name = self.extract_pure_name(display_name)

                    if pure_name != display_name:
                        extracted_count += 1
                        if extracted_count <= 5:  # 只打印前5个示例，进一步减少日志输出
                            self.update_log(
                                f"🔍 智能提取: {display_name} → {pure_name}"
                            )

                    # 确保pure_name正确，即使与display_name相同
                    if not pure_name:
                        pure_name = "朋友"

                    # 只添加有效名称的联系人
                    self.contacts.append(
                        {
                            "name": pure_name,
                            "original_display": display_name,
                            "nickname": nickname,
                            "remark": remark,
                            "tags": tags,  # 添加标签信息
                            "original_name": pure_name,
                            "is_filtered": is_filtered,  # 添加筛选标志
                        }
                    )

            # 统计有标签的联系人
            tagged_contacts_count = sum(
                1 for contact in self.contacts if contact.get("tags")
            )
            self.update_log(f"🏷️  从微信获取到 {tagged_contacts_count} 位有标签的联系人")

            # 更新过滤列表和界面
            self._update_contacts(self.contacts, self.contacts.copy())
            self.run_in_main_thread(self.update_contact_listbox, self.filtered_contacts)

            # 状态和日志更新（保持你当前的风格）
            self.update_status(
                f"✅ 联系人获取完成！共找到 {len(self.contacts)} 位（其中 {filtered_count} 位被筛选）",
                "#52C41A",
            )
            self.update_log(
                f"✅ 联系人加载完成 - 共 {len(self.contacts)} 位联系人 | 包含 {filtered_count} 位被筛选联系人"
            )

            # 更新标签下拉框
            self.run_in_main_thread(self.update_tag_combobox)

        except Exception as e:
            error_msg = str(e)
            self.update_status(f"❌ 获取联系人失败: {error_msg[:50]}...", "#FF4D4F")
            self.update_log(f"❌ 联系人读取失败: {error_msg}")
            self.update_log(f"📋 错误详情: {traceback.format_exc()}")

            # 提供更详细的错误信息和解决方案
            solutions = ["1. 确保微信PC端已登录且处于前台"]

            if "GetFriendDetails" in error_msg:
                solutions.append("2. 尝试更新wxauto库到最新版本")
                solutions.append("3. 检查wxauto版本是否兼容您的微信版本")
            elif "未初始化" in error_msg:
                solutions.append("2. 请先等待微信客户端初始化完成")
                solutions.append("3. 尝试重启应用程序")

            messagebox.showerror(
                "错误",
                f"获取联系人失败: {error_msg}\n\n建议解决方案：\n{chr(10).join(solutions)}",
            )
        finally:
            self.is_refreshing = False

    def update_dashboard_metrics(self):
        """更新仪表盘数据"""
        if not hasattr(self, "metric_total_var"):
            return

        # 总联系人
        total_contacts = len(self.contacts)
        self.metric_total_var.set(str(total_contacts))

        # 今日已发送 (实际上是本次任务已发送)
        success_count = len(self.send_results.get("success", []))
        if hasattr(self, "metric_today_var"):
            self.metric_today_var.set(str(success_count))

        # 仪表盘 KPI 更新
        if hasattr(self, "kpi_sent"):
            self.kpi_sent.set(str(success_count))
        if hasattr(self, "kpi_contacts"):
            self.kpi_contacts.set(str(total_contacts))

        # 待处理 (简单的计算：总数 - 已发送 - 失败 - 被删)
        failed_count = len(self.send_results.get("failed", []))
        deleted_count = len(self.send_results.get("deleted", []))

        # 计算成功率
        processed_total = success_count + deleted_count
        if processed_total > 0:
            rate = (success_count / processed_total) * 100
            rate_str = f"{rate:.1f}%"
        else:
            rate_str = "0.0%"

        if hasattr(self, "metric_rate_var"):
            self.metric_rate_var.set(rate_str)

        # 仪表盘 KPI 更新
        if hasattr(self, "kpi_rate"):
            self.kpi_rate.set(rate_str)

        # 如果正在发送中，待处理应该是剩余的任务数
        # 这里使用 selected_contacts 的长度来计算更准确
        if (
            self.is_sending
            and hasattr(self, "selected_contacts")
            and self.selected_contacts
        ):
            total_tasks = len(self.selected_contacts)
            # 当前进度 index 从 1 开始，所以剩余 = 总数 - 当前index
            # 注意：current_contact_index 是正在处理的，所以待处理应该是 total - index (如果 index=1, total=10, 剩余9个)
            # 但如果 index 还没开始 (0)，则剩余 10 个
            # 如果任务刚开始，processed 可能还没更新

            # 使用更直接的方式：
            processed = success_count + failed_count + deleted_count
            pending = total_tasks - processed
            if pending < 0:
                pending = 0
            self.metric_pending_var.set(str(pending))
        else:
            # 非发送状态，显示0或保持
            self.metric_pending_var.set("0")

        # 更新连接状态
        if self.is_initialized:
            self.connection_status.config(text="在线", foreground="#10B981")
        else:
            self.connection_status.config(text="离线", foreground="#EF4444")

    def update_contact_listbox(self, contacts):
        """更新联系人列表 (Treeview)"""
        # 清空现有条目
        for item in self.contact_tree.get_children():
            self.contact_tree.delete(item)

        for i, contact in enumerate(contacts):
            # 获取数据
            name = contact.get("original_display", "")
            remark = contact.get("remark", "")  # 假设有remark字段，如果没有则为空
            tags = contact.get("tags", "")
            is_filtered = contact.get("is_filtered", False)

            # 状态显示
            status = "已过滤" if is_filtered else "待发送"

            # 插入数据，使用索引作为iid方便后续查找
            self.contact_tree.insert(
                "", "end", iid=str(i), values=(name, remark, tags, status)
            )

            # 如果被过滤，可以设置特定的tag来改变样式（需要配置tag_configure）
            # if is_filtered:
            #     self.contact_tree.item(str(i), tags=('filtered',))

        # 更新仪表盘数据
        self.update_dashboard_metrics()

    def sleep_with_check(self, seconds):
        """可中断的sleep函数，在等待过程中检查发送状态"""
        if seconds <= 0:
            return

        start_time = time.time()
        while time.time() - start_time < seconds:
            # 检查是否需要停止发送
            if not self.is_sending:
                return

            # 检查是否需要暂停
            while self.is_paused:
                if not self.is_sending:
                    return
                time.sleep(0.1)  # 暂停时每0.1秒检查一次状态

            time.sleep(0.1)  # 正常等待时每0.1秒检查一次状态

    def toggle_pause_resume(self):
        """暂停/恢复发送"""
        if not self.is_sending:
            return

        self.is_paused = not self.is_paused

        if self.is_paused:
            self.pause_button.config(
                text="▶️ 继续发送", style="Primary.TButton"
            )  # 样式变更为Primary，提示点击继续
            self.update_log("⏸️ 任务暂停...")
            self.update_status("⏸️ 任务暂停", "#FAAD14")
            # 仪表盘状态同步
            if (
                hasattr(self, "dash_status_label")
                and self.dash_status_label.winfo_exists()
            ):
                self.dash_status_label.configure(
                    text="⏸️ 任务已暂停，点击继续恢复", style="BadgeWarning.TLabel"
                )
        else:
            self.pause_button.config(
                text="⏸️ 暂停", style="Secondary.TButton"
            )  # 恢复默认样式
            self.update_log("▶️ 任务继续...")
            self.update_status("▶️ 任务继续", "#1890FF")
            # 仪表盘状态同步
            if (
                hasattr(self, "dash_status_label")
                and self.dash_status_label.winfo_exists()
            ):
                self.dash_status_label.configure(
                    text="▶️ 正在执行任务...", style="BadgeInfo.TLabel"
                )

    def sending_thread(self):
        """发送线程"""
        try:
            if self.config["send_from_selected"].get():
                self.selected_contacts = self.get_contacts_from_selected()
                if not self.selected_contacts:
                    messagebox.showwarning("警告", "请先选择一个联系人作为开始点！")
                    return
                self.update_log(
                    f"📤 模式：从选中联系人开始发送 - 共 {len(self.selected_contacts)} 位"
                )
            else:
                self.selected_contacts = self.get_selected_contacts()
                if not self.selected_contacts:
                    messagebox.showwarning("警告", "请先选择联系人！")
                    return
                self.update_log(
                    f"📤 模式：只发送选中联系人 - 共 {len(self.selected_contacts)} 位"
                )
            if not self.image_paths:
                if not messagebox.askyesno(
                    "警告", "未选择有效图片，是否继续发送文字消息？"
                ):
                    return
            self.is_sending = True
            self.is_paused = False
            self.run_in_main_thread(
                lambda: self.pause_button.config(state=tk.NORMAL, text="暂停发送")
            )
            # 如果开启了跳过筛选，先计算实际需要发送的总数
            skip_filtered = self.config["skip_filtered_contacts"].get()
            actual_tasks = []
            if skip_filtered:
                actual_tasks = [
                    c for c in self.selected_contacts if not c.get("is_filtered", False)
                ]
                # 如果没有有效任务
                if not actual_tasks:
                    self.is_sending = False
                    self.run_in_main_thread(
                        lambda: self.pause_button.config(
                            state=tk.DISABLED, text="暂停发送"
                        )
                    )
                    messagebox.showinfo(
                        "提示", "所选联系人全部被过滤规则排除，无须发送。"
                    )
                    return
            else:
                actual_tasks = self.selected_contacts

            total = len(actual_tasks)
            self.send_results = {"success": [], "failed": [], "deleted": []}
            send_mode = (
                "从选中联系人开始"
                if self.config["send_from_selected"].get()
                else "选中联系人"
            )
            self.update_status(
                f"📤 开始{send_mode}发送 - 共 {total} 位有效联系人", "#5B8FF9"
            )
            self.update_log("💡 提示：按Space暂停/恢复 | P/End终止")

            # 记录开始时间
            self.start_time = time.time()
            self.kimi_current_template = None
            self.kimi_last_batch_index = -1
            self.limit_override_risk_boost = False
            daily_limit_override = False

            # 防风控：动态暂停初始化
            msgs_sent_since_last_pause = 0
            if self.config["anti_risk_mode"].get():
                next_pause_threshold = random.randint(
                    15, 25
                )  # 开启防风控：每15-25人暂停
            else:
                next_pause_threshold = 100  # 默认：每100人暂停

            # 本次发送已处理的联系人集合（防止重复发送）
            processed_contacts = set()

            for i, contact in enumerate(actual_tasks, 1):
                if not self.is_sending:
                    break

                # 每日上限检查
                current_success = self.get_daily_sent_count()
                daily_limit = self.config["daily_limit"].get()
                if current_success >= daily_limit:
                    if not daily_limit_override:
                        self.update_log(f"⚠️ 已达到每日发送上限 ({daily_limit}人)")
                        should_continue = self.ask_yesno_safe(
                            "防风控提示",
                            f"已达到每日发送上限 ({daily_limit}人)。\n继续发送会增加账号风险，是否继续本轮任务？",
                        )
                        if should_continue:
                            daily_limit_override = True
                            self.limit_override_risk_boost = True
                            next_pause_threshold = random.randint(8, 12)
                            self.update_log("⚠️ 你选择继续发送：已自动切换为更保守节奏")
                            self.update_status(
                                "⚠️ 达上限后继续发送，已进入强化防风控节奏", "#FAAD14"
                            )
                        else:
                            self.update_log(
                                f"🛑 已达到每日发送上限 ({daily_limit}人)，按设置停止任务"
                            )
                            break

                # 生成唯一标识 (昵称+备注+名字) - 兼容导入的联系人(无昵称备注)
                # 修改：对于只有名字的情况，不应该视为重复，除非完全一致
                # contact_key = (contact.get('nickname', ''), contact.get('remark', ''), contact.get('name', ''))

                # 只有当 nickname 或 remark 存在时，才使用它们作为去重依据
                # 如果只有 name (例如导入的联系人)，则不去重，或者仅当 name 也完全相同时才去重
                # 但这里的 contact['name'] 已经是处理过的 pure_name (可能都是"朋友")
                # 所以必须使用 original_display 或 original_name 来区分

                orig_name = (
                    contact.get("original_display")
                    or contact.get("original_name")
                    or contact.get("name")
                )
                contact_key = (
                    contact.get("nickname", ""),
                    contact.get("remark", ""),
                    orig_name,
                )

                if contact_key in processed_contacts:
                    # 再次确认：如果是从文件导入的，可能没有nickname/remark，只有name
                    # 如果 name 都是 "朋友"，那肯定会重复
                    # 所以关键是 original_display 必须不同
                    pass
                    # self.update_log(f"⚠️ 跳过重复联系人: {contact['name']}")
                    # continue

                # 暂时移除这个去重逻辑，因为对于大量导入的联系人（只有名字），很容易误判
                # 特别是如果名字提取后都变成了"朋友"
                # processed_contacts.add(contact_key)

                # 暂停逻辑
                if self.is_paused:
                    self.update_log("⏸️ 任务已暂停，等待继续...")

                    while self.is_paused:
                        self.sleep_with_check(0.1)  # 暂停时每0.1秒检查一次状态
                        if not self.is_sending:
                            break

                    # 暂停恢复后的处理：尝试刷新连接，防止COM错误
                    if self.is_sending:
                        try:
                            # 尝试一个轻量级操作来测试连接是否正常
                            # 如果连接断开，这里可能会抛出异常
                            self.update_log("🔄 正在恢复任务，检查微信连接...")
                            # 重新初始化 wxauto 对象，确保获取最新的窗口句柄
                            # 这是一个比较激进但稳妥的做法，特别是针对 "事件无法调用任何订户" 这类错误
                            try:
                                self.wx = WeChat()
                                self.update_log("✅ 微信连接已刷新")
                            except Exception as e:
                                self.update_log(f"⚠️ 刷新微信连接失败: {e}")
                        except Exception:
                            pass

                if not self.is_sending:
                    break

                # 检查是否需要跳过被筛选的联系人
                is_filtered = contact.get("is_filtered", False)
                skip_filtered = self.config["skip_filtered_contacts"].get()

                # 由于已经提前过滤了 actual_tasks，这里的循环内过滤逻辑可以简化
                # 但为了双重保险（防止配置在运行时被修改），保留检查，但通常不会命中
                if is_filtered and skip_filtered:
                    self.current_contact_index = i
                    # 进度条不需要跳过了，因为根本没进循环
                    continue

                self.current_contact_index = i
                self.update_status(f"📤 正在发送给 ({i}/{total}) {contact['name']}")
                success, status = self.send_to_contact_auto(contact, i)
                if success:
                    self.send_results["success"].append(contact)
                    self.increment_daily_sent_count(1)
                else:
                    # 避免将被删联系人双重计入failed列表
                    # 如果状态中包含被删信息，send_to_contact_auto已经将其加入deleted列表
                    if "已被删除" in status or "消息被拒收" in status:
                        pass  # 已经在deleted列表中，不再处理
                    else:
                        self.send_results["failed"].append(
                            {"contact": contact, "reason": status}
                        )

                # 发送后更新进度和时间
                # 进度 = 当前索引(i)
                # 注意：如果跳过了很多重复的，i 可能会很大，但进度条应该反映"已处理/总数"
                # 这里的 total 是 actual_tasks 的长度（剔除过滤后的），而 i 是 actual_tasks 的索引
                # 所以 update_progress(i, total) 是正确的，它反映了遍历进度
                # 但用户希望看到的是 成功+被删 的数量

                # 如果我们想让进度条显示 成功+被删 的数量，那就不应该用 i
                # 但进度条的目的是显示任务完成了多少（还有多少没做）
                # 这里的 total 是所有要处理的任务数，i 是当前处理到第几个
                # 如果有跳过的重复联系人，它们虽然没发，但也算"处理过了"（被跳过）
                # 所以 i/total 反映的是任务完成度，这是对的。

                # 用户困惑的是：任务完成了，显示的数字却是 98/115
                # 这是因为 update_progress(len(success), total) 在最后被调用了
                # 我们应该在最后更新时，使用 (success + deleted) 或者直接使用 total (如果全部完成了)

                self.update_progress(i, total)

                # 实时更新仪表盘
                self.run_in_main_thread(self.update_dashboard_metrics)

                # 实时更新结果页面 (如果已打开)
                self.run_in_main_thread(self.update_results_view)

                # 智能动态暂停 (防风控核心逻辑)
                msgs_sent_since_last_pause += 1
                if msgs_sent_since_last_pause >= next_pause_threshold and i < total:
                    # 计算休息时间
                    if self.limit_override_risk_boost:
                        pause_duration = random.randint(120, 240)
                        self.update_log(
                            f"🛡️ [强化防风控] 已连续处理 {msgs_sent_since_last_pause} 人，安全休息 {pause_duration} 秒..."
                        )
                        next_pause_threshold = random.randint(8, 12)
                    elif self.config["anti_risk_mode"].get():
                        # 防风控模式：休息 1-3 分钟 (60-180秒)
                        pause_duration = random.randint(60, 180)
                        self.update_log(
                            f"🛡️ [防风控] 已连续处理 {msgs_sent_since_last_pause} 人，安全休息 {pause_duration} 秒..."
                        )
                        # 更新下一次阈值 (15-25人)
                        next_pause_threshold = random.randint(15, 25)
                    else:
                        # 普通模式：休息 20 秒
                        pause_duration = 20
                        self.update_log(
                            f"⏳ 已发送 {i} 人，休息 {pause_duration} 秒..."
                        )
                        # 保持每100人休息一次的节奏
                        msgs_sent_since_last_pause = 0
                        # 注意：如果这里重置为0，那么 next_pause_threshold 应该保持 100
                        # 或者简单地：next_pause_threshold = 100 (不变)

                    self.update_status(f"⏳ 安全休息中... ({i}/{total})")

                    # 执行休息
                    self.sleep_with_check(pause_duration)

                    self.update_log("▶️ 休息结束，继续发送...")
                    msgs_sent_since_last_pause = 0

            # 发送完成
            self.is_sending = False
            self.is_paused = False
            self.limit_override_risk_boost = False
            self.run_in_main_thread(
                lambda: self.pause_button.config(state=tk.DISABLED, text="暂停发送")
            )

            # 更新进度条显示：使用总处理人数 (成功 + 被删)
            final_processed = len(self.send_results["success"]) + len(
                self.send_results["deleted"]
            )
            self.update_progress(final_processed, total)

            self.update_status(
                f"✅ 发送完成！成功 {len(self.send_results['success'])} 条", "#52C41A"
            )
            # 自动跳转到结果页面并刷新
            self.run_in_main_thread(lambda: self.show_page("results"))
            self.run_in_main_thread(self.update_results_view)
        except Exception as e:
            self.is_sending = False
            self.is_paused = False
            self.limit_override_risk_boost = False
            self.run_in_main_thread(
                lambda: self.pause_button.config(state=tk.DISABLED, text="暂停发送")
            )
            error_str = str(e)
            self.update_status(f"❌ 发送出错: {error_str}", "#FF4D4F")
            self.update_log(f"❌ 发送异常: {error_str}")
            self.run_in_main_thread(
                lambda: messagebox.showerror("错误", f"发送过程出错: {error_str}")
            )

    def release_ctrl_key(self):
        """强制释放 Ctrl 键，防止粘贴后按键卡住"""
        try:
            # VK_CONTROL = 0x11, KEYEVENTF_KEYUP = 0x0002
            ctypes.windll.user32.keybd_event(0x11, 0, 0x0002, 0)
        except Exception:
            pass

    def _check_deletion_and_handle(self, contact, detection_result):
        """检查删除检测结果并处理，返回 (is_deleted, result_tuple)"""
        if detection_result["is_deleted"]:
            contact_with_deletion_info = contact.copy()
            contact_with_deletion_info["deletion_info"] = detection_result
            self._append_send_result("deleted", contact_with_deletion_info)
            return True, (False, f"已被删除：{detection_result['reason']}")
        return False, None

    def _send_single_image(self, contact, img_path):
        """发送单张图片，返回 (success, message)"""
        try:
            self.wx.SendFiles(img_path)
            self.release_ctrl_key()
            self.update_log(f"📷 图片已发送给 {contact['name']}: {os.path.basename(img_path)}")
            self.sleep_with_check(self.config["delays"]["image_to_msg"])

            if not self.is_sending:
                return False, "发送已停止"

            # 图片发送后检测是否被删除
            detection_result = self.is_deleted_contact()
            is_deleted, result = self._check_deletion_and_handle(contact, detection_result)
            if is_deleted:
                return result

            return True, "图片发送成功"
        except (OSError, RuntimeError) as e:
            img_error = str(e)
            if "-2147220991" in img_error or "事件无法调用任何订户" in img_error:
                raise
            self.update_log(f"❌ 图片发送失败: {img_error}")
            # 检测是否因为被删除
            detection_result = self.is_deleted_contact(img_error)
            is_deleted, result = self._check_deletion_and_handle(contact, detection_result)
            if is_deleted:
                return result
            return False, f"图片发送失败: {img_error}"

    def _send_text_message(self, contact, message):
        """发送文字消息，返回 (success, message)"""
        try:
            self.wx.SendMsg(message)
            self.release_ctrl_key()
            self.update_log(f"✅ 消息已发送给 {contact['name']}")

            if not self.is_sending:
                return False, "发送已停止"

            return True, "消息发送成功"
        except (OSError, RuntimeError) as e:
            error_msg = str(e)
            if "-2147220991" in error_msg or "事件无法调用任何订户" in error_msg:
                raise
            self.update_log(f"❌ 消息发送失败给 {contact['name']}: {error_msg}")
            detection_result = self.is_deleted_contact(error_msg)
            is_deleted, result = self._check_deletion_and_handle(contact, detection_result)
            if is_deleted:
                return result
            return False, error_msg

    def send_to_contact_auto(self, contact, contact_index=1):
        """发送给单个联系人（增强版，完整异常处理+多张图片支持+COM错误重试）"""
        max_retries = 2
        for attempt in range(max_retries):
            try:
                # 1. 生成个性化消息（增强版，支持{name}占位符）
                # 确保使用纯净的名字，再次调用extract_pure_name确保万无一失
                pure_name = self.extract_pure_name(contact["name"])
                greeting = self.format_greeting(pure_name)
                emoji = random.choice(self.config["emojis"])
                template = self.get_active_message_template(contact_index)

                # greeting 现在可能为 "" (针对无效名) 或 "张三" / "Chamy"
                try:
                    message = template.format(
                        greeting=greeting, name=greeting, emoji=emoji
                    )
                except Exception:
                    message = f"{greeting}，你好！{emoji}"

                # 修复可能出现的"四朋友"问题：如果greeting本身已经是数字结尾（如"初四"），再加"朋友"会变成"初四朋友"
                # 但这里我们主要担心的是 filter_patterns 没有处理干净，导致残留数字
                # 比如 "张三4" -> extract_pure_name -> "张三4" -> format_greeting -> "张三4"
                # 然后 message 变成 "张三4朋友"
                # 我们在发送前做最后一次检查

                # 移除message中可能出现的数字+朋友组合（如果是误判）
                # 但实际上更好的做法是在 extract_pure_name 中处理干净
                pass

                # 2. 随机延迟后搜索联系人（避免微信风控）
                search_min = self.config["delays"]["search_min"]
                search_max = self.config["delays"]["search_max"]

                # 防风控模式：搜索延迟翻倍
                if (
                    self.config["anti_risk_mode"].get()
                    or self.limit_override_risk_boost
                ):
                    search_min *= 2
                    search_max *= 2

                delay = random.uniform(search_min, search_max)
                self.sleep_with_check(delay)
                search_name = contact["original_display"] or contact["name"]
                self.wx.ChatWith(search_name, exact=False, force=True)
                self.sleep_with_check(
                    0.5
                )  # 等待聊天窗口完全加载（缩短等待时间以提高速度）

                # 检查是否需要停止发送
                if not self.is_sending:
                    return False, "发送已停止"

                # 聊天窗口打开后检测是否被删除
                detection_result = self.is_deleted_contact()
                is_deleted, result = self._check_deletion_and_handle(contact, detection_result)
                if is_deleted:
                    return result

                # 获取发送顺序
                send_order = self.config["send_order"].get()

                # 3. 发送逻辑（根据选择的顺序发送）
                items_to_send = []
                if send_order == "images_first":
                    items_to_send.extend(("image", p) for p in self.image_paths)
                    items_to_send.append(("text", message))
                else:
                    items_to_send.append(("text", message))
                    items_to_send.extend(("image", p) for p in self.image_paths)

                for item_type, item in items_to_send:
                    if item_type == "image":
                        if os.path.exists(item):
                            success, msg = self._send_single_image(contact, item)
                            if not success:
                                return False, msg
                    else:
                        success, msg = self._send_text_message(contact, item)
                        if not success:
                            return False, msg

                    if not self.is_sending:
                        return False, "发送已停止"

                    # 文字发送后需要延迟再发图片
                    if item_type == "text" and send_order == "text_first" and self.image_paths:
                        self.sleep_with_check(self.config["delays"]["image_to_msg"])

                # 最终检测+随机延迟（避免频繁发送）
                self.sleep_with_check(0.5)

                # 检查是否需要停止发送
                if not self.is_sending:
                    return False, "发送已停止"

                detection_result = self.is_deleted_contact()
                if detection_result["is_deleted"]:
                    contact_with_deletion_info = contact.copy()
                    contact_with_deletion_info["deletion_info"] = detection_result
                    self._append_send_result("deleted", contact_with_deletion_info)
                    return False, f"已被删除（最终检测）：{detection_result['reason']}"

                # 随机延迟，模拟人工操作
                delay = random.uniform(
                    self.config["delays"]["min"], self.config["delays"]["max"]
                )
                self.sleep_with_check(delay)

                # 检查是否需要停止发送
                if not self.is_sending:
                    return False, "发送已停止"

                return True, "发送成功"

            except (OSError, RuntimeError) as e:
                # 捕获COM错误和运行时错误，支持重试
                error_str = str(e)
                if "-2147220991" in error_str or "事件无法调用任何订户" in error_str:
                    self.update_log(
                        f"⚠️ 检测到COM连接错误 (尝试 {attempt + 1}/{max_retries}): {error_str}"
                    )
                    if attempt < max_retries - 1:
                        self.update_log("🔄 正在重新初始化微信连接并重试...")
                        try:
                            # 重新初始化 wxauto 对象
                            self.wx = WeChat()
                            self.sleep_with_check(1)
                            continue  # 进入下一次循环重试
                        except (OSError, RuntimeError) as re_init_error:
                            self.update_log(f"❌ 重连失败: {re_init_error}")

                # 如果是其他错误，或者重试次数用尽，则返回失败
                self.update_log(f"❌ 发送给 {contact['name']} 整体失败: {error_str}")
                detection_result = self.is_deleted_contact(error_str)
                if detection_result["is_deleted"]:
                    contact_with_deletion_info = contact.copy()
                    contact_with_deletion_info["deletion_info"] = detection_result
                    self._append_send_result("deleted", contact_with_deletion_info)
                    return False, f"已被删除：{detection_result['reason']}"
                return False, f"发送异常: {error_str}"
            except ValueError as e:
                # 数据格式错误，不重试
                error_str = str(e)
                self.update_log(f"❌ 发送给 {contact['name']} 数据错误: {error_str}")
                return False, f"数据错误: {error_str}"

        return False, "重试次数已耗尽"

    def format_greeting(self, name):
        """格式化问候语：返回纯名字或空字符串（由模板负责补全称呼）"""
        name = name.strip()
        name = re.sub(r"\s+", "", name)

        # 如果名字无效或本身就是通用称呼，返回空字符串
        if not name or name == "朋友":
            return ""

        # 纯数字或全是特殊符号，返回空字符串
        if re.fullmatch(r"\d+", name) or re.fullmatch(r"[^\u4e00-\u9fa5a-zA-Z]+", name):
            return ""

        # 纯英文姓名处理：保留英文名（如 Chamy -> Chamy）
        # 如果您希望英文名不显示，则此处返回 ""
        if re.fullmatch(r"[a-zA-Z]+", name):
            return ""

        # 5. 检测是否包含明显的称谓
        # 称谓_list = ['阿姨', '叔叔', '同学']
        # for 称谓 in 称谓_list:
        #     if name.endswith(称谓):
        #         return name # 称谓直接返回名字，不加"朋友"

        # 6. 包含明显无效的关键词
        # invalid_keywords = ['直访', '数学', '的', '@', '介绍', '销售', '客服', '大班', '中班']
        invalid_keywords = self.config.get("invalid_keywords", [])
        if any(kw in name for kw in invalid_keywords):
            return ""

        # 6.1 特殊处理"推荐"
        if "推荐" in name:
            # 如果名字中包含"推荐"，则去掉推荐及其后面的内容
            # 例如 "AAA🍭雨轩💗程雨欣推荐" -> "AAA🍭雨轩💗"
            name = name.split("推荐")[0]
            # 再次进行清理，防止分割后末尾有特殊符号
            name = self.filter_final_text(name)
            # 如果清理后名字为空，则返回空字符串
            if not name:
                return ""

        # 7. 检测是否为纯年级词汇（使用配置中的 exact_match_invalid_names）
        if name in self.config.get("exact_match_invalid_names", []):
            return ""

        # 正常情况：直接返回名字（模板中会加上称呼）
        return name

    def _extract_message_text(self, msg):
        """从不同类型的消息对象中提取消息文本"""
        msg_text = ""
        msg_type = type(msg).__name__

        if isinstance(msg, dict):
            msg_text = msg.get("content", "") or msg.get("Content", "") or ""
            msg_type = msg.get("type", "") or msg.get("Type", "") or msg_type
        elif hasattr(msg, "__str__"):
            try:
                msg_str = str(msg)
                # 尝试使用反射获取消息内容
                for attr in ["content", "Content", "msg", "Msg", "message", "Message"]:
                    if hasattr(msg, attr):
                        msg_text = getattr(msg, attr)
                        if msg_text:
                            break

                # 如果反射失败，尝试正则提取
                if not msg_text:
                    match = re.search(r"\((.*?)\)", msg_str)
                    if match:
                        msg_text = match.group(1)
                    else:
                        msg_text = msg_str
            except (AttributeError, ValueError, TypeError):
                msg_text = ""

        return msg_text, msg_type

    def _check_keywords_in_text(self, text, keywords):
        """检查文本中是否包含任何关键词，返回 (found, keyword)"""
        if not text:
            return False, ""
        for kw in keywords:
            if kw in text:
                return True, kw
        return False, ""

    def _get_chat_info_text(self):
        """获取ChatInfo文本"""
        try:
            chat_info = self.wx.ChatInfo()
            if isinstance(chat_info, dict):
                for key, value in chat_info.items():
                    if isinstance(value, str) and value:
                        return value
            elif isinstance(chat_info, str):
                return chat_info
        except (OSError, RuntimeError, AttributeError) as e:
            self.update_log(f"⚠ ChatInfo检测出错: {str(e)}")
        return ""

    def _get_recent_messages(self):
        """获取最近的消息列表"""
        messages = []
        try:
            if hasattr(self.wx, "GetMsgList"):
                messages = self.wx.GetMsgList()
            if not messages and hasattr(self.wx, "GetAllMessage"):
                messages = self.wx.GetAllMessage()
        except (OSError, RuntimeError, AttributeError):
            try:
                if hasattr(self.wx, "GetAllMessage"):
                    messages = self.wx.GetAllMessage()
            except (OSError, RuntimeError, AttributeError):
                pass

        if len(messages) > 30:
            messages = messages[-30:]
        return messages

    def is_deleted_contact(self, error_msg=None):
        """判断是否为被删除的用户（简化版，多维度检测）"""
        detected_results = {
            "is_deleted": False,
            "reason": "",
            "full_info": "",
            "detection_method": "",
        }

        # 1. 检查错误信息
        if error_msg:
            found, kw = self._check_keywords_in_text(error_msg, self.config["delete_keywords"])
            if found:
                self.update_log(f"⚠️ 发现被删联系人（错误信息关键词）: {kw}")
                return {"is_deleted": True, "reason": kw, "full_info": error_msg, "detection_method": "错误信息检测"}

        # 2. 检查ChatInfo
        chat_info_text = self._get_chat_info_text()
        if chat_info_text:
            found, kw = self._check_keywords_in_text(chat_info_text, self.config["delete_keywords"])
            if found:
                self.update_log(f"✅ ChatInfo中检测到关键词: {kw}")
                return {"is_deleted": True, "reason": kw, "full_info": chat_info_text, "detection_method": "ChatInfo检测"}

        # 3. 检查最近消息
        try:
            self.sleep_with_check(0.5)
            if not self.is_sending:
                return detected_results

            messages = self._get_recent_messages()
            for i, msg in enumerate(messages):
                msg_text, msg_type = self._extract_message_text(msg)
                if not msg_text:
                    continue

                # 跳过自己发送的非系统消息
                msg_type_lower = str(msg_type).lower()
                if "self" in msg_type_lower and "system" not in msg_type_lower:
                    continue

                # 检查关键词
                found, kw = self._check_keywords_in_text(msg_text, self.config["delete_keywords"])
                if found:
                    self.update_log(f"✅ 消息 {i + 1} 中检测到关键词: {kw}")
                    return {"is_deleted": True, "reason": kw, "full_info": msg_text, "detection_method": f"消息检测（消息 {i + 1}）"}
        except (OSError, RuntimeError, AttributeError) as e:
            self.update_log(f"⚠ 消息检测出错: {str(e)}")

        self.update_log("✅ 未检测到删除标志，联系人正常")
        return detected_results

    def get_selected_contacts(self):
        """获取选中的联系人 (Treeview)"""
        selected_items = self.contact_tree.selection()
        if not selected_items:
            return []

        selected_contacts = []
        for iid in selected_items:
            try:
                idx = int(iid)
            except Exception:
                try:
                    idx = int(iid.replace("I", ""), 16) - 1
                except Exception:
                    continue
            if 0 <= idx < len(self.filtered_contacts):
                selected_contacts.append(self.filtered_contacts[idx])
        return selected_contacts

    def get_contacts_from_selected(self):
        """从选中位置开始的所有联系人 (Treeview)"""
        selected_items = self.contact_tree.selection()
        if not selected_items:
            return self.filtered_contacts

        try:
            first_iid = selected_items[0]
            try:
                start_idx = int(first_iid)
            except Exception:
                start_idx = int(first_iid.replace("I", ""), 16) - 1
            if start_idx < 0:
                start_idx = 0
            return self.filtered_contacts[start_idx:]
        except Exception:
            return self.filtered_contacts

    def sort_contacts_az(self):
        """按字母排序"""
        if not self.contacts:
            messagebox.showwarning("警告", "没有联系人可以排序！")
            return
        try:
            from pypinyin import lazy_pinyin

            self.contacts.sort(key=lambda x: "".join(lazy_pinyin(x["name"])))
        except ImportError:
            self.contacts.sort(key=lambda x: x["name"])
        self._update_contacts(self.contacts, self.contacts.copy())
        self.update_contact_listbox(self.filtered_contacts)
        self.status_label.config(fg="#52C41A", text="✅ 联系人已按字母排序完成")

    def update_tag_combobox(self):
        """更新标签下拉框"""
        if not self.contacts:
            return
        # 收集所有唯一标签
        all_tags = set()
        for contact in self.contacts:
            tags = contact.get("tags", "")
            if tags:
                # 处理多个标签的情况（假设标签之间用逗号分隔）
                tag_list = [tag.strip() for tag in tags.split(",")]
                all_tags.update(tag_list)
        # 转换为列表并排序
        tag_list = sorted(list(all_tags))
        # 更新下拉框选项
        self.tag_combobox["values"] = ["所有标签"] + tag_list
        self.tag_combobox.set("所有标签")

    def filter_contacts_by_tag(self):
        """按标签筛选联系人"""
        if not self.contacts:
            messagebox.showwarning("提示", "没有联系人可筛选！")
            return
        selected_tag = self.tag_var.get()
        if selected_tag == "所有标签":
            self._update_contacts(self.contacts, self.contacts.copy())
        else:
            # 筛选包含所选标签的联系人
            filtered = []
            for contact in self.contacts:
                tags = contact.get("tags", "")
                if tags:
                    tag_list = [tag.strip() for tag in tags.split(",")]
                    if selected_tag in tag_list:
                        filtered.append(contact)
            with self._data_lock:
                self.filtered_contacts = filtered
        # 更新联系人列表
        self.update_contact_listbox(self.filtered_contacts)
        self.update_log(
            f"🏷️  已按标签 '{selected_tag}' 筛选联系人，共 {len(self.filtered_contacts)} 位"
        )

    def clear_tag_filter(self):
        """清除标签筛选"""
        self.filtered_contacts = self.contacts.copy()
        self.tag_var.set("所有标签")
        self.update_contact_listbox(self.filtered_contacts)
        self.update_log("🔄 已清除标签筛选")

    def search_contacts(self, event):
        """搜索并定位联系人（不修改列表，只滚动定位）"""
        keyword = self.search_entry.get().strip().lower()
        if not keyword:
            return

        # 记录上次搜索状态，支持"查找下一个"
        if (
            not hasattr(self, "last_search_keyword")
            or self.last_search_keyword != keyword
        ):
            self.last_search_keyword = keyword
            self.last_search_index = -1

        # 从上次位置之后开始查找
        start_index = self.last_search_index + 1
        found = False

        # 遍历当前列表（filtered_contacts可能是全部，也可能是按标签筛选后的）
        for i in range(start_index, len(self.filtered_contacts)):
            contact = self.filtered_contacts[i]
            # 搜索匹配：名字、原始名、备注
            if (
                keyword in contact["name"].lower()
                or keyword in contact["original_display"].lower()
                or keyword in contact.get("remark", "").lower()
            ):
                # 找到匹配项
                iid = str(i)  # 我们的iid就是索引字符串

                # 1. 选中
                self.contact_tree.selection_set(iid)
                # 2. 聚焦
                self.contact_tree.focus(iid)
                # 3. 滚动可见
                self.contact_tree.see(iid)

                # 更新状态
                self.last_search_index = i
                self.status_label.config(
                    fg="#5B8FF9",
                    text=f"🔍 已定位到: {contact['name']} ({i + 1}/{len(self.filtered_contacts)})",
                )
                found = True
                break

        # 如果后面没找到，且不是从头开始的，尝试从头找一次（循环查找）
        if not found and start_index > 0:
            for i in range(0, start_index):
                contact = self.filtered_contacts[i]
                if (
                    keyword in contact["name"].lower()
                    or keyword in contact["original_display"].lower()
                    or keyword in contact.get("remark", "").lower()
                ):
                    iid = str(i)
                    self.contact_tree.selection_set(iid)
                    self.contact_tree.focus(iid)
                    self.contact_tree.see(iid)

                    self.last_search_index = i
                    self.status_label.config(
                        fg="#5B8FF9",
                        text=f"🔍 已定位到: {contact['name']} ({i + 1}/{len(self.filtered_contacts)})",
                    )
                    found = True
                    break

        if not found:
            self.status_label.config(
                fg="#FAAD14", text=f"⚠️ 未找到包含 '{keyword}' 的联系人"
            )
            # 重置索引以便下次重新开始
            self.last_search_index = -1

    def clear_search(self):
        """清空搜索"""
        self.search_entry.delete(0, tk.END)
        self.filtered_contacts = self.contacts.copy()
        self.update_contact_listbox(self.filtered_contacts)
        self.status_label.config(
            fg="#52C41A", text=f"✅ 搜索已清空 - 共 {len(self.contacts)} 位联系人"
        )

    def select_all_contacts(self):
        """全选联系人 (Treeview)"""
        children = self.contact_tree.get_children()
        if children:
            self.contact_tree.selection_set(children)
        self.update_log("✅ 已全选所有联系人")
        # 触发选中更新事件
        self._on_contact_select_update_info(None)

    def deselect_all_contacts(self):
        """取消全选 (Treeview)"""
        children = self.contact_tree.get_children()
        if children:
            self.contact_tree.selection_remove(children)
        self.update_log("✅ 已取消全选")
        # 触发选中更新事件
        self._on_contact_select_update_info(None)

    def update_send_button_text(self):
        """更新发送按钮文字"""
        if self.config["send_from_selected"].get():
            self.start_button.config(text="从选中联系人开始全自动发送")
        else:
            self.start_button.config(text="开始全自动发送")

    def on_hotkey_press(self, event):
        """快捷键事件"""
        if self.is_sending:
            self.stop_sending()
            self.update_log(f"⏹️ 通过快捷键 {event.keysym} 终止发送")
            messagebox.showinfo("提示", "已终止发送任务！")

    def start_sending(self):
        """开始发送"""
        if self.is_sending:
            messagebox.showwarning("警告", "发送任务正在进行中！")
            return
        if messagebox.askyesno("确认", "即将开始全自动发送，是否继续？"):
            threading.Thread(target=self.sending_thread, daemon=True).start()

    def stop_sending(self):
        """停止发送"""
        if self.is_sending:
            self.is_sending = False
            self.is_paused = False
            self.pause_button.config(state=tk.DISABLED, text="暂停发送")
            self.status_label.config(fg="#FF4D4F", text="⏹️ 发送已终止")
            self.update_log("⏹️ 发送任务已手动终止")

    def show_results(self):
        """显示发送结果（现代化UI）"""
        # 如果窗口已存在，直接显示
        if hasattr(self, "result_win") and self.result_win.winfo_exists():
            self.result_win.lift()
            return

        result_win = tk.Toplevel(self.root)
        self.result_win = result_win  # 保存引用
        result_win.title("发送结果统计")
        result_win.geometry("900x700")
        result_win.configure(bg="#F0F2F5")  # 使用主背景色

        # 按照新的逻辑计算总数
        success = len(self.send_results["success"])
        failed = len(self.send_results["failed"])
        deleted = len(self.send_results["deleted"])

        # 总计尝试 = 成功 + 失败 + 被删
        total = success + failed + deleted

        # 主容器
        main_frame = ttk.Frame(result_win, padding="25")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 1. 顶部数据卡片区域
        stats_frame = ttk.Frame(main_frame)
        stats_frame.pack(fill=tk.X, pady=(0, 25))

        # 创建4个卡片
        stats_card = tk.Frame(stats_frame, bg="white", padx=20, pady=20)
        stats_card.pack(fill=tk.X)

        # 定义变量以便后续更新
        self.res_total = tk.StringVar(
            value=str(
                len(self.send_results["success"]) + len(self.send_results["deleted"])
            )
        )
        self.res_success = tk.StringVar(value=str(success))
        self.res_failed = tk.StringVar(value=str(failed))
        self.res_deleted = tk.StringVar(value=str(deleted))
        self.res_deleted_rate = tk.StringVar(value="(0.0%)")

        # 计算初始删除率 (用户要求：删除率 = 被删 / 成功)
        if success > 0:
            rate = (deleted / success) * 100
            self.res_deleted_rate.set(f"({rate:.1f}%)")
        elif deleted > 0:
            self.res_deleted_rate.set("(100.0%)")

        def create_stat_item(parent, col, title, value_var, color, sub_var=None):
            frame = tk.Frame(parent, bg="white")
            frame.grid(row=0, column=col, sticky="ew", padx=10)
            parent.grid_columnconfigure(col, weight=1)

            tk.Label(
                frame, text=title, font=("Segoe UI", 10), fg="#8C8C8C", bg="white"
            ).pack(anchor="w")

            val_frame = tk.Frame(frame, bg="white")
            val_frame.pack(anchor="w", pady=(5, 0))

            tk.Label(
                val_frame,
                textvariable=value_var,
                font=("Segoe UI", 24, "bold"),
                fg=color,
                bg="white",
            ).pack(side=tk.LEFT)

            if sub_var:
                tk.Label(
                    val_frame,
                    textvariable=sub_var,
                    font=("Segoe UI", 12),
                    fg="#FF4D4F",
                    bg="white",
                    padx=5,
                ).pack(side=tk.LEFT, activebackground="white")

        # 修改显示标题：总计尝试 -> 已处理人数
        create_stat_item(
            stats_card, 0, "已处理人数(成功+被删)", self.res_total, "#333333"
        )
        create_stat_item(stats_card, 1, "发送成功", self.res_success, "#52C41A")
        create_stat_item(stats_card, 2, "发送失败", self.res_failed, "#FF4D4F")
        create_stat_item(
            stats_card,
            3,
            "发现被删",
            self.res_deleted,
            "#FAAD14",
            self.res_deleted_rate,
        )

        # 2. 详细列表区域
        list_frame = tk.Frame(main_frame, bg="white")
        list_frame.pack(fill=tk.BOTH, expand=True)

        # 选项卡
        tab_frame = tk.Frame(list_frame, bg="#FAFAFA", height=40)
        tab_frame.pack(fill=tk.X)

        # 列表头
        columns = ("name", "remark", "origin")
        headers = ("姓名", "备注", "原始昵称")

        # 滚动条容器
        tree_container = tk.Frame(list_frame)
        tree_container.pack(fill=tk.BOTH, expand=True)

        self.result_tree = ttk.Treeview(
            tree_container, columns=columns, show="headings", selectmode="browse"
        )

        # 设置列
        self.result_tree.heading("name", text="姓名")
        self.result_tree.heading("remark", text="备注")
        self.result_tree.heading("origin", text="原始昵称")

        self.result_tree.column("name", width=150)
        self.result_tree.column("remark", width=300)
        self.result_tree.column("origin", width=200)

        # 滚动条
        vsb = ttk.Scrollbar(
            tree_container, orient="vertical", command=self.result_tree.yview
        )
        self.result_tree.configure(yscrollcommand=vsb.set)

        self.result_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        # Tag样式
        self.result_tree.tag_configure("success", background="#F6FFED")  # 浅绿
        self.result_tree.tag_configure("failed", background="#FFF1F0")  # 浅红
        self.result_tree.tag_configure("deleted", background="#FFFBE6")  # 浅黄

        # 底部按钮栏
        btn_frame = tk.Frame(main_frame, bg="#F0F2F5", pady=15)
        btn_frame.pack(fill=tk.X)

        # 导出报告功能
        def export_report():
            if (
                not self.send_results["success"]
                and not self.send_results["failed"]
                and not self.send_results["deleted"]
            ):
                messagebox.showinfo("提示", "暂无数据可导出")
                return

            filename = f"发送报告_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            with open(filename, "w", encoding="utf-8") as f:
                f.write("=== 微信自动发送报告 ===\n")
                f.write(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(
                    f"总发送人数: {len(self.send_results['success']) + len(self.send_results['deleted'])}\n"
                )
                f.write(f"发送成功: {len(self.send_results['success'])}\n")
                f.write(f"发送失败: {len(self.send_results['failed'])}\n")
                f.write(f"发现被删: {len(self.send_results['deleted'])}\n\n")

                # 计算删除率
                del_rate = "0.00%"
                suc_cnt = len(self.send_results["success"])
                if suc_cnt > 0:
                    del_rate = (
                        f"{(len(self.send_results['deleted']) / suc_cnt) * 100:.2f}%"
                    )
                elif len(self.send_results["deleted"]) > 0:
                    del_rate = "100.00%"
                f.write(f"删除率: {del_rate}\n\n")

                f.write("--- 被删/拉黑名单 ---\n")
                for contact in self.send_results["deleted"]:
                    info = contact.get("deletion_info", {})
                    f.write(
                        f"姓名: {contact['name']}, 备注: {contact.get('remark', '')}, 原因: {info.get('reason', '未知')}\n"
                    )

                f.write("\n--- 发送失败名单 ---\n")
                for item in self.send_results["failed"]:
                    contact = item["contact"]
                    f.write(f"姓名: {contact['name']}, 原因: {item['reason']}\n")

            messagebox.showinfo("成功", f"报告已导出至: {filename}")
            # 自动打开文件
            os.startfile(filename)

        # 刷新按钮（虽然现在是自动刷新，但保留手动刷新按钮也不错）
        # ttk.Button(btn_frame, text="🔄 刷新数据", command=self.update_results_view).pack(side=tk.RIGHT, padx=5)

        # 导出按钮
        ttk.Button(btn_frame, text="💾 导出报告", command=export_report).pack(
            side=tk.RIGHT, padx=5
        )

        # 初始化数据
        self.update_results_view()

        # 自动定时刷新（每秒刷新一次）
        def auto_refresh():
            if result_win.winfo_exists():
                self.update_results_view()
                # 只有当发送任务进行中才继续刷新
                if self.is_sending:
                    result_win.after(1000, auto_refresh)

        # 启动自动刷新
        if self.is_sending:
            result_win.after(1000, auto_refresh)

    def _create_result_tab(self, notebook, title, data, prefix):
        """创建结果标签页"""
        frame = ttk.Frame(notebook)
        notebook.add(frame, text=title)
        text = scrolledtext.ScrolledText(
            frame,
            wrap=tk.WORD,
            font=("Microsoft YaHei UI", 11),
            width=110,
            height=20,
            bg="white",
            relief=tk.FLAT,
        )
        text.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        for item in data:
            line = f"{prefix} {item['name']}"
            if (
                item.get("original_display")
                and item["original_display"] != item["name"]
            ):
                line += f" ({item['original_display']})"

            # 如果是已删除联系人，显示完整删除信息
            if title == "已删除您的人" and item.get("deletion_info"):
                deletion_info = item["deletion_info"]
                line += f" - 删除原因：{deletion_info['reason']}"
                # line += f" - 检测方法：{deletion_info['detection_method']}" # 简化显示

            text.insert(tk.END, line + "\n")

    def _create_failed_tab(self, notebook, title, data):
        """创建失败列表标签页"""
        frame = ttk.Frame(notebook)
        notebook.add(frame, text=title)
        text = scrolledtext.ScrolledText(
            frame,
            wrap=tk.WORD,
            font=("Microsoft YaHei UI", 11),
            width=110,
            height=20,
            bg="white",
            relief=tk.FLAT,
        )
        text.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        for item in data:
            contact = item["contact"]
            line = f"❌ {contact['name']}"
            if (
                contact.get("original_display")
                and contact["original_display"] != contact["name"]
            ):
                line += f" ({contact['original_display']})"
            line += f" - {item['reason']}"
            text.insert(tk.END, line + "\n")

    def save_results(self, parent):
        """保存结果（去掉猪头表情，包含耗时和删除率）"""
        default_filename = f"发送结果_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        path = filedialog.asksaveasfilename(
            title="保存发送结果",
            defaultextension=".txt",
            initialfile=default_filename,
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
        )
        if path:
            try:
                total = (
                    len(self.send_results["success"])
                    + len(self.send_results["failed"])
                    + len(self.send_results["deleted"])
                )

                # 计算耗时
                elapsed_time_str = "未记录"
                if hasattr(self, "start_time"):
                    elapsed_seconds = time.time() - self.start_time
                    minutes = int(elapsed_seconds // 60)
                    seconds = int(elapsed_seconds % 60)
                    elapsed_time_str = f"{minutes}分{seconds}秒"

                # 计算删除率 (基于总处理人数计算，即 成功+失败+被删)
                # 总发送人数 = 成功 + 失败 + 被删
                # 但根据用户最新要求：总发送人数是：成功人数加被删除人数
                # 这里我们保持内部逻辑 total = sum(all)，但在写入文件时按用户要求修改

                # 用户要求：总发送人数是：成功人数加被删除人数
                # 这可能意味着用户认为"失败"的不算"发送"过
                user_defined_total = len(self.send_results["success"]) + len(
                    self.send_results["deleted"]
                )

                deletion_rate = "0.00%"
                # 用户要求：删除率 = 被删 / 成功
                # 注意：如果成功为0，避免除以零错误
                success_count = len(self.send_results["success"])
                if success_count > 0:
                    deletion_rate = f"{(len(self.send_results['deleted']) / success_count) * 100:.2f}%"
                elif len(self.send_results["deleted"]) > 0:
                    # 如果只有被删没有成功，删除率可视作 100% 或者无穷大，这里显示 >100% 或其他提示
                    # 或者如果用户意图是 被删 / (成功+被删) = 50/(100+50) = 33% ?
                    # 用户说 "发送成功100人，失败50，删除50，删除率是50%"
                    # 这意味着公式是：删除率 = 被删 / 成功 = 50 / 100 = 50%
                    deletion_rate = "100.00%"  # 只有被删没有成功的情况

                # 重新修正逻辑：确保符合用户的50%预期
                # Case: Success=100, Failed=50, Deleted=50 -> Rate=50%
                # Formula: Deleted / Success * 100%
                if success_count > 0:
                    deletion_rate = f"{(len(self.send_results['deleted']) / success_count) * 100:.2f}%"

                with open(path, "w", encoding="utf-8") as f:
                    f.write("批量消息发送结果\n")
                    f.write("=" * 50 + "\n")
                    f.write(
                        f"发送时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                    )
                    f.write(f"发送耗时: {elapsed_time_str}\n")
                    f.write("-" * 50 + "\n")
                    f.write(f"总发送人数：{user_defined_total}\n")
                    f.write(f"发送成功: {len(self.send_results['success'])}\n")
                    f.write(f"发送失败: {len(self.send_results['failed'])}\n")
                    f.write(f"发现被删: {len(self.send_results['deleted'])}\n")
                    f.write(f"删除率:   {deletion_rate}\n")
                    f.write("=" * 50 + "\n\n")

                    f.write("成功列表:\n")
                    for c in self.send_results["success"]:
                        f.write(f"✅ {c['name']}")
                        if (
                            c.get("original_display")
                            and c["original_display"] != c["name"]
                        ):
                            f.write(f"（{c['original_display']}）")
                        f.write("\n")
                    f.write("\n失败列表:\n")
                    for item in self.send_results["failed"]:
                        contact = item["contact"]
                        f.write(f"❌ {contact['name']}")
                        if (
                            contact.get("original_display")
                            and contact["original_display"] != contact["name"]
                        ):
                            f.write(f"（{contact['original_display']}）")
                        f.write(f" - {item['reason']}\n")
                    f.write("\n已删除列表:\n")
                    for c in self.send_results["deleted"]:
                        f.write(f"⚠ {c['name']}")
                        if (
                            c.get("original_display")
                            and c["original_display"] != c["name"]
                        ):
                            f.write(f"（{c['original_display']}）")
                        # 写入删除原因
                        if c.get("deletion_info"):
                            f.write(f" - 原因: {c['deletion_info']['reason']}")
                        f.write("\n")
                messagebox.showinfo("成功", f"结果已保存到:\n{path}", parent=parent)
            except Exception as e:
                messagebox.showerror("错误", f"保存失败: {e}", parent=parent)

    def export_contacts_to_txt(self):
        """导出联系人（去掉猪头表情）"""
        if not self.contacts:
            messagebox.showwarning("警告", "没有联系人可以导出！")
            return
        path = filedialog.asksaveasfilename(
            title="导出联系人到TXT",
            defaultextension=".txt",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
        )
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write("联系人列表\n")
                    f.write("=" * 50 + "\n")
                    f.write(
                        f"导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                    )
                    f.write(f"联系人总数: {len(self.contacts)}\n\n")
                    for i, c in enumerate(self.contacts, 1):
                        f.write(f"{i}. {c['name']}\n")
                        if c["original_display"] != c["name"]:
                            f.write(f"   原始备注: {c['original_display']}\n")
                        # 导出标签信息
                        tags = c.get("tags", "")
                        if tags:
                            f.write(f"   标签: {tags}\n")
                        f.write("\n")
                messagebox.showinfo(
                    "成功", f"已导出 {len(self.contacts)} 位联系人到:\n{path}"
                )
                self.update_log(f"📤 联系人已导出到: {path}")
            except Exception as e:
                messagebox.showerror("错误", f"导出失败: {e}")

    def import_contacts_from_txt(self):
        """导入联系人"""
        path = filedialog.askopenfilename(
            title="从TXT导入联系人",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
        )
        if path:
            try:
                encodings = ["utf-8", "gbk", "gb2312"]
                content = None
                for enc in encodings:
                    try:
                        with open(path, "r", encoding=enc) as f:
                            content = f.read()
                        break
                    except Exception:
                        continue
                if not content:
                    raise Exception("无法识别文件编码")
                lines = content.split("\n")

                # 提取导入的联系人
                imported_contacts = []
                current_name = None
                current_original_display = None
                current_tags = ""

                for line in lines:
                    line = line.strip()
                    if line and not line.startswith(
                        ("=", "#", "导出时间", "联系人总数", "联系人列表")
                    ):
                        if re.match(r"^\d+\.\s+.+", line):
                            # 如果是新的联系人行，先处理之前的联系人（如果有）
                            if current_name:
                                # 添加之前的联系人
                                imported_contacts.append(
                                    {
                                        "name": current_name,
                                        "original_display": current_original_display
                                        if current_original_display
                                        else current_name,
                                        "original_name": current_name,
                                        "remark": "",  # 不导入备注，设为空字符串
                                        "nickname": "",
                                        "tags": current_tags,  # 添加标签信息
                                        "is_filtered": any(
                                            kw in current_name
                                            or kw in (current_original_display or "")
                                            for kw in self.config["filter_keywords"]
                                        ),
                                    }
                                )
                            # 提取新联系人的名称
                            current_name = line.split(".", 1)[1].strip()
                            current_original_display = None
                            current_tags = ""
                        elif line.startswith("原始备注:") and current_name:
                            # 处理原始备注行，只提取实际的备注内容，不包含"原始备注:"前缀
                            original_display = line[5:].strip()  # 5是"原始备注:"的长度
                            current_original_display = original_display
                        elif line.startswith("标签:") and current_name:
                            # 处理标签行，提取标签内容
                            current_tags = line[3:].strip()  # 3是"标签:"的长度
                        elif line and not current_name:
                            # 处理没有序号的联系人行
                            current_name = line
                            current_original_display = None
                            current_tags = ""

                # 添加最后一个联系人（如果有）
                if current_name:
                    imported_contacts.append(
                        {
                            "name": current_name,
                            "original_display": current_original_display
                            if current_original_display
                            else current_name,
                            "original_name": current_name,
                            "remark": "",  # 不导入备注，设为空字符串
                            "nickname": "",
                            "tags": current_tags,  # 添加标签信息
                            "is_filtered": any(
                                kw in current_name
                                or kw in (current_original_display or "")
                                for kw in self.config["filter_keywords"]
                            ),
                        }
                    )

                if imported_contacts:
                    # 直接添加新联系人，不去重
                    new_contacts = imported_contacts
                    duplicate_count = 0

                    # 添加新联系人
                    if new_contacts:
                        self.contacts.extend(new_contacts)
                        self.filtered_contacts = self.contacts.copy()
                        self.update_contact_listbox(self.filtered_contacts)
                        self.update_tag_combobox()  # 更新标签下拉框

                        # 显示导入结果
                        result_msg = f"已导入 {len(new_contacts)} 位联系人"

                        messagebox.showinfo("成功", result_msg)
                        self.update_log(f"📥 {result_msg}")
                    else:
                        messagebox.showinfo("提示", "没有联系人导入")
                else:
                    messagebox.showwarning("警告", "未找到有效联系人！")
            except Exception as e:
                messagebox.showerror("错误", f"导入失败: {e}")

    def run(self):
        """运行主程序"""
        self.root.mainloop()

    def open_remark_modifier(self):
        """打开微信联系人备注修改工具"""
        try:
            self.update_log("📝 正在打开微信联系人备注修改工具...")

            # 定义联系人刷新回调函数
            def refresh_contacts_callback(updated_contacts):
                """联系人刷新回调，更新主程序的联系人列表"""
                self.update_log(
                    f"🔄 从备注修改工具收到联系人更新，共 {len(updated_contacts)} 位联系人"
                )
                # 更新主程序的联系人列表
                self.contacts = updated_contacts
                self.filtered_contacts = updated_contacts.copy()
                self.update_contact_listbox(self.filtered_contacts)

            # 创建并显示备注修改窗口，传递当前联系人列表和刷新回调
            self.remark_modifier = WeChatRemarkModifier(
                contacts=self.contacts, refresh_callback=refresh_contacts_callback
            )
            self.update_log("✅ 微信联系人备注修改工具已打开")
        except Exception as e:
            self.update_log(f"❌ 打开备注修改工具失败: {str(e)}")
            messagebox.showerror("错误", f"打开备注修改工具失败: {e}")


