# -*- coding: utf-8 -*-
"""
基础模块 - 提供共用的基类和组件
"""

import json
import logging
import os
import threading
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, scrolledtext, ttk

logger = logging.getLogger(__name__)


class ScrollingStatus(tk.Frame):
    """滚动状态栏组件"""

    def __init__(self, parent, bg, fg, font, height=24, **kwargs):
        super().__init__(parent, bg=bg, height=height, **kwargs)
        self.pack_propagate(False)

        self.bg_color = bg
        self.fg_color = fg
        self.font = font

        self.canvas = tk.Canvas(self, bg=bg, height=height, highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.text_id = None
        self.current_text = ""
        self.offset = 0
        self.scrolling = False
        self.after_id = None

        self.set_message("系统就绪", "info")

    def set_message(self, text, type="info"):
        """设置消息并根据类型改变颜色"""
        colors = {
            "info": ("#064E3B", "#34D399"),
            "success": ("#064E3B", "#34D399"),
            "warning": ("#451a03", "#fcd34d"),
            "error": ("#450a0a", "#fca5a5"),
        }

        bg, fg = colors.get(type, colors["info"])

        self.bg_color = bg
        self.fg_color = fg
        self.current_text = text

        self.configure(bg=bg)
        self.canvas.configure(bg=bg)

        if self.after_id:
            self.after_cancel(self.after_id)
            self.after_id = None

        self.canvas.delete("all")
        self.offset = 10

        self.text_id = self.canvas.create_text(
            self.offset, 12, text=text, font=self.font, fill=fg, anchor="w",
        )

        bbox = self.canvas.bbox(self.text_id)
        if bbox:
            text_width = bbox[2] - bbox[0]
            canvas_width = self.winfo_width() or 200

            if text_width > canvas_width - 20:
                self.scrolling = True
                self._animate_scroll()
            else:
                self.scrolling = False

    def _animate_scroll(self):
        """滚动动画"""
        bbox = self.canvas.bbox(self.text_id)
        if not bbox:
            return

        text_width = bbox[2] - bbox[0]
        canvas_width = self.winfo_width()

        self.offset -= 1
        self.canvas.move(self.text_id, -1, 0)

        if self.offset < -text_width:
            self.offset = canvas_width
            self.canvas.coords(self.text_id, self.offset, 12)

        self.after_id = self.after(30, self._animate_scroll)


class BaseWeChatTool:
    """微信工具基类，提供共用方法"""

    def run_in_main_thread(self, func, *args, **kwargs):
        """在主线程中执行函数"""
        self.root.after(0, lambda: func(*args, **kwargs))

    def update_status(self, text, color=None):
        """线程安全的更新状态栏"""

        def _update():
            if hasattr(self, "status_label") and self.status_label.winfo_exists():
                if isinstance(self.status_label, ScrollingStatus):
                    msg_type = "info"
                    if "成功" in text or "完成" in text or "在线" in text or (color and "green" in str(color)):
                        msg_type = "success"
                    elif "错误" in text or "失败" in text or "停止" in text or (color and "red" in str(color)):
                        msg_type = "error"
                    elif "警告" in text or (color and "orange" in str(color)):
                        msg_type = "warning"
                    self.status_label.set_message(text, msg_type)
                else:
                    self.status_label.config(text=text)
                    if color:
                        self.status_label.config(fg=color)

        self.run_in_main_thread(_update)

    def update_log(self, msg):
        """更新日志（线程安全版）"""
        if threading.current_thread() is threading.main_thread():
            self._update_log_impl(msg)
        else:
            self.run_in_main_thread(self._update_log_impl, msg)

    def _update_log_impl(self, msg):
        """日志更新的实际实现"""
        if hasattr(self, "log_text") and self.log_text.winfo_exists():
            try:
                timestamp = datetime.now().strftime("%H:%M:%S")
                self.log_text.config(state=tk.NORMAL)
                self.log_text.insert(tk.END, f"[{timestamp}] {msg}\n")
                self.log_text.see(tk.END)
                self.log_text.config(state=tk.DISABLED)
            except Exception:
                pass

    def load_app_config(self):
        """加载应用配置"""
        self.app_config_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "config", "app_config.json"
        )
        self.secrets_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "config", "secrets.json"
        )
        try:
            if os.path.exists(self.app_config_file):
                with open(self.app_config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for key in [
                        "filter_keywords", "clean_keywords", "title_keywords",
                        "grade_keywords", "noise_keywords", "compound_surnames",
                        "delete_keywords", "invalid_keywords", "exact_match_invalid_names",
                        "filter_chars", "filter_patterns",
                    ]:
                        if key in data:
                            self.config[key] = data[key]

                    if "message_template" in data:
                        self.config["message_template"] = data["message_template"]

                    if "anti_risk_mode" in data:
                        self.config["anti_risk_mode"].set(data["anti_risk_mode"])
                    if "daily_limit" in data:
                        self.config["daily_limit"].set(data["daily_limit"])
                    if "daily_sent_count" in data:
                        try:
                            self.config["daily_sent_count"] = max(0, int(data["daily_sent_count"]))
                        except (ValueError, TypeError):
                            self.config["daily_sent_count"] = 0
                    if "daily_sent_date" in data:
                        self.config["daily_sent_date"] = str(data["daily_sent_date"] or "")
                    if "kimi_rewrite_enabled" in data:
                        self.config["kimi_rewrite_enabled"].set(data["kimi_rewrite_enabled"])
                    if "kimi_base_url" in data:
                        self.config["kimi_base_url"].set(data["kimi_base_url"])
                    if "kimi_model" in data:
                        self.config["kimi_model"].set(data["kimi_model"])
                    if "kimi_batch_size" in data:
                        self.config["kimi_batch_size"].set(data["kimi_batch_size"])
                    if "kimi_system_prompt" in data:
                        self.config["kimi_system_prompt"].set(data["kimi_system_prompt"])
                    if self.config["kimi_model"].get().strip() in ["", "moonshot-v1-8k", "kimi2.5"]:
                        self.config["kimi_model"].set("kimi-k2-turbo-preview")

                # 从 secrets.json 加载敏感配置
                if os.path.exists(self.secrets_file):
                    with open(self.secrets_file, "r", encoding="utf-8") as f:
                        secrets = json.load(f)
                        if "kimi_api_key" in secrets:
                            self.config["kimi_api_key"].set(secrets["kimi_api_key"])

                self.update_log("✅ 已加载自定义配置")
        except json.JSONDecodeError as e:
            logger.error(f"配置文件格式错误: {e}")
        except IOError as e:
            logger.error(f"加载应用配置失败: {e}")

    def save_app_config(self):
        """保存应用配置"""
        try:
            data = {}
            for key in [
                "filter_keywords", "clean_keywords", "title_keywords",
                "grade_keywords", "noise_keywords", "compound_surnames",
                "delete_keywords", "invalid_keywords", "exact_match_invalid_names",
                "filter_chars", "filter_patterns",
            ]:
                data[key] = self.config[key]

            data["message_template"] = self.config.get("message_template", "")
            data["anti_risk_mode"] = self.config["anti_risk_mode"].get()
            data["daily_limit"] = self.config["daily_limit"].get()
            data["daily_sent_count"] = int(self.config.get("daily_sent_count", 0))
            data["daily_sent_date"] = self.config.get("daily_sent_date", "")
            data["kimi_rewrite_enabled"] = self.config["kimi_rewrite_enabled"].get()
            data["kimi_base_url"] = self.config["kimi_base_url"].get()
            data["kimi_model"] = self.config["kimi_model"].get()
            data["kimi_batch_size"] = self.config["kimi_batch_size"].get()
            data["kimi_system_prompt"] = self.config["kimi_system_prompt"].get()

            with open(self.app_config_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)

            # 敏感配置保存到 secrets.json
            secrets_data = {"kimi_api_key": self.config["kimi_api_key"].get()}
            secrets_file = getattr(self, "secrets_file", os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "..", "config", "secrets.json"
            ))
            with open(secrets_file, "w", encoding="utf-8") as f:
                json.dump(secrets_data, f, ensure_ascii=False, indent=4)
        except (IOError, PermissionError) as e:
            logger.error(f"保存应用配置失败: {e}")

    def save_rule(self, key):
        """保存单项规则"""
        try:
            text_widget = self.rule_text_widgets[key]
            content = text_widget.get("1.0", tk.END).strip()
            new_rules = [line.strip() for line in content.split("\n") if line.strip()]
            self.config[key] = new_rules
            self.save_app_config()
            messagebox.showinfo("成功", "规则已保存！")
        except (IOError, KeyError) as e:
            messagebox.showerror("错误", f"保存失败: {e}")

    def open_rule_manager(self):
        """打开规则管理窗口"""
        try:
            if not hasattr(self, "config"):
                self.load_app_config()

            rule_window = tk.Toplevel(self.root)
            rule_window.title("过滤规则管理")
            rule_window.geometry("800x600")
            rule_window.configure(bg="#F8F9FA")

            notebook = ttk.Notebook(rule_window)
            notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

            categories = [
                ("过滤关键词", "filter_keywords", "过滤包含这些词的联系人"),
                ("内容清洗", "clean_keywords", "发送时去除名字中的这些词"),
                ("称谓去除", "title_keywords", "去除名字中的称谓"),
                ("年级去除", "grade_keywords", "去除名字中的年级信息"),
                ("干扰词库", "noise_keywords", "去除名字中的公司/机构等干扰词"),
                ("复姓库", "compound_surnames", "识别复姓"),
                ("删除检测", "delete_keywords", "检测被删/拉黑的关键词"),
                ("无效关键词", "invalid_keywords", "直接判定为无效的名字"),
            ]

            self.rule_text_widgets = {}

            for title, key, desc in categories:
                frame = ttk.Frame(notebook, padding=10)
                notebook.add(frame, text=title)

                ttk.Label(
                    frame, text=f"说明：{desc}",
                    font=("Segoe UI", 10, "italic"), foreground="#6B7280",
                ).pack(anchor="w", pady=(0, 10))
                ttk.Label(
                    frame, text="（每行一个关键词，支持实时保存）",
                    font=("Segoe UI", 9), foreground="#9CA3AF",
                ).pack(anchor="w", pady=(0, 5))

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
                    btn_frame, text=f"保存 [{title}]",
                    command=lambda k=key: self.save_rule(k),
                ).pack(side=tk.RIGHT)

        except Exception as e:
            messagebox.showerror("错误", f"打开规则管理失败: {e}")
