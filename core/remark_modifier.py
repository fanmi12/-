# -*- coding: utf-8 -*-
"""WeChatRemarkModifier 备注修改工具"""

import json
import logging
import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from core.base import ScrollingStatus, BaseWeChatTool

try:
    from wxauto import WeChat
except ImportError:
    WeChat = None

import comtypes

logger = logging.getLogger(__name__)


class WeChatRemarkModifier(BaseWeChatTool):
    """微信联系人备注修改工具"""

    def __init__(self, contacts=None, refresh_callback=None):
        # 创建独立窗口
        self.root = tk.Toplevel()
        self.root.title("微信联系人备注修改工具")
        self.root.geometry("1000x600")
        self.root.configure(bg="white")

        # 核心状态变量
        self.wx = None
        self.is_initialized = False
        self.is_refreshing = False

        # 数据存储
        self.contacts = contacts or []  # 使用外部传入的联系人列表或空列表
        self.filtered_contacts = self.contacts.copy()
        self.selected_contact = None

        # 标记是否使用外部传入的联系人列表
        self.use_external_contacts = True if contacts else False

        # 刷新回调函数，用于通知主程序更新联系人
        self.refresh_callback = refresh_callback

        # 设置界面
        self.setup_ui()

        # 初始化微信（后台线程）
        threading.Thread(target=self.init_wechat, daemon=True).start()

        # 如果有外部联系人，直接更新列表
        if self.contacts:
            self.update_contact_listbox()
            self.update_log(
                f"📥 已使用外部传入的联系人列表，共 {len(self.contacts)} 位联系人"
            )

    def setup_ui(self):
        """设置界面布局 - 现代化 Dashboard 风格 (Design System Hardening)"""
        # --- 样式定义 ---
        style = ttk.Style()
        style.theme_use("clam")  # 使用 clam 主题以便自定义颜色

        # 杂志编辑风色板 (与主程序一致)
        colors = {
            "bg_main": "#F4EFE6",        # 纸张背景
            "bg_card": "#FBF8F2",        # 卡片暖白
            "text_primary": "#1A1A18",   # 近黑墨色
            "text_secondary": "#6B6B60", # 灰色
            "accent": "#8B2500",         # 杂志红
            "primary": "#8B2500",        # 主按钮红
            "primary_hover": "#A03000",
            "success": "#2D6A4F",        # 深绿
            "border": "#C4B9A8",         # 边框米灰
            "hover": "#E8E0D4",          # 悬停米色
        }

        # 配置通用样式
        style.configure(
            ".",
            background=colors["bg_main"],
            foreground=colors["text_primary"],
            font=("Segoe UI", 10),
        )
        style.configure("TFrame", background=colors["bg_main"])

        # 卡片样式 (白色背景)
        style.configure("Card.TFrame", background=colors["bg_card"], relief="flat")

        # 标题样式 (层级强化)
        style.configure(
            "H1.TLabel",
            font=("Segoe UI", 24, "bold"),
            background=colors["bg_main"],
            foreground=colors["text_primary"],
        )
        style.configure(
            "H2.TLabel",
            font=("Segoe UI", 16, "bold"),
            background=colors["bg_card"],
            foreground=colors["text_primary"],
        )
        style.configure(
            "Subtitle.TLabel",
            font=("Segoe UI", 10),
            background=colors["bg_main"],
            foreground=colors["text_secondary"],
        )

        # 按钮样式 (统一高度与 Padding)
        # Primary
        style.configure(
            "Primary.TButton",
            font=("Segoe UI", 10, "bold"),
            background=colors["primary"],
            foreground="white",
            borderwidth=0,
            padding=(24, 10),
        )
        style.map(
            "Primary.TButton",
            background=[("active", colors["primary_hover"]), ("disabled", "#E5E7EB")],
            foreground=[("disabled", "#9CA3AF")],
        )

        # Outline -> Secondary
        style.configure(
            "Outline.TButton",
            font=("Segoe UI", 10),
            background=colors["bg_card"],
            foreground=colors["text_primary"],
            borderwidth=1,
            bordercolor=colors["border"],
            padding=(24, 10),
        )
        style.map("Outline.TButton", background=[("active", colors["hover"])])

        # Success (Action)
        style.configure(
            "Success.TButton",
            font=("Segoe UI", 10, "bold"),
            background=colors["success"],
            foreground="white",
            borderwidth=0,
            padding=(24, 10),
        )

        # 标签页样式
        style.configure("TNotebook", background=colors["bg_main"], borderwidth=0)
        style.configure(
            "TNotebook.Tab",
            padding=[16, 12],
            font=("Segoe UI", 10),
            background=colors["bg_main"],
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", colors["bg_card"])],
            foreground=[("selected", colors["primary"])],
        )

        # --- 主窗口布局 ---
        self.root.configure(bg=colors["bg_main"])

        # 顶部 Header 区域
        header_frame = ttk.Frame(self.root, padding="32 32 32 16")
        header_frame.pack(fill=tk.X)

        # 标题与状态
        header_top = ttk.Frame(header_frame)
        header_top.pack(fill=tk.X, expand=True)

        title_box = ttk.Frame(header_top)
        title_box.pack(side=tk.LEFT)
        ttk.Label(title_box, text="WeChat Assistant Dashboard", style="H1.TLabel").pack(
            anchor="w"
        )
        ttk.Label(
            title_box, text="微信自动化营销管理平台", style="Subtitle.TLabel"
        ).pack(anchor="w")

        # 顶部状态卡片 (模拟 Metrics)
        status_frame = ttk.Frame(header_top)
        status_frame.pack(side=tk.RIGHT)

        self.status_label = tk.Label(
            status_frame,
            text="正在初始化...",
            font=("Segoe UI", 10),
            bg="#DEF7EC",
            fg="#03543F",
            padx=10,
            pady=5,
            relief="flat",
        )  # 使用Label实现圆角背景效果较难，这里用颜色区分
        self.status_label.pack(side=tk.RIGHT)

        # 统计卡片区域 (Metrics Grid)
        metrics_frame = ttk.Frame(self.root, padding="24 0 24 12")
        metrics_frame.pack(fill=tk.X)

        # 封装一个创建 Metric Card 的函数
        def create_metric_card(parent, title, value_var, icon, change, trend="up"):
            card = ttk.Frame(parent, style="Card.TFrame", padding=15)
            card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 15))

            header = ttk.Frame(card, style="Card.TFrame")
            header.pack(fill=tk.X, pady=(0, 5))
            ttk.Label(
                header,
                text=title,
                font=("Segoe UI", 9, "bold"),
                foreground=colors["text_secondary"],
                background=colors["bg_card"],
            ).pack(side=tk.LEFT)
            ttk.Label(
                header, text=icon, font=("Segoe UI", 12), background=colors["bg_card"]
            ).pack(side=tk.RIGHT)

            # 使用 textvariable 绑定
            if isinstance(value_var, tk.StringVar):
                ttk.Label(
                    card,
                    textvariable=value_var,
                    font=("Segoe UI", 18, "bold"),
                    background=colors["bg_card"],
                ).pack(anchor="w")
            else:
                ttk.Label(
                    card,
                    text=value_var,
                    font=("Segoe UI", 18, "bold"),
                    background=colors["bg_card"],
                ).pack(anchor="w")

            trend_color = colors["success"] if trend == "up" else "#EF4444"
            trend_text = f"{'↑' if trend == 'up' else '↓'} {change}"
            ttk.Label(
                card,
                text=trend_text,
                font=("Segoe UI", 9),
                foreground=trend_color,
                background=colors["bg_card"],
            ).pack(anchor="w")

            return card

        # 占位数据，实际可绑定变量
        self.metric_total_var = tk.StringVar(value="Loading...")
        self.metric_today_var = tk.StringVar(value="0")
        self.metric_pending_var = tk.StringVar(value="0")

        create_metric_card(
            metrics_frame, "总联系人", self.metric_total_var, "👥", "+0%", "up"
        )
        create_metric_card(
            metrics_frame, "今日已发送", self.metric_today_var, "📨", "+0%", "up"
        )
        create_metric_card(
            metrics_frame, "待处理", self.metric_pending_var, "⏳", "-0%", "down"
        )

        # 最后一个不用右边距
        last_card = ttk.Frame(metrics_frame, style="Card.TFrame", padding=15)
        last_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ttk.Label(
            last_card,
            text="系统状态",
            font=("Segoe UI", 9, "bold"),
            foreground=colors["text_secondary"],
            background=colors["bg_card"],
        ).pack(anchor="w")
        self.connection_status = ttk.Label(
            last_card,
            text="检查中...",
            font=("Segoe UI", 12, "bold"),
            foreground="#F59E0B",
            background=colors["bg_card"],
        )
        self.connection_status.pack(anchor="w", pady=(5, 0))

        # --- 主内容区域 (Grid Layout) ---
        content_container = ttk.Frame(self.root, padding="24 12 24 24")
        content_container.pack(fill=tk.BOTH, expand=True)

        # 使用 PanedWindow 或 Grid 分割左右
        # 这里用 Grid 模拟 1:2 的布局

        # 左侧：联系人列表卡片
        left_card = ttk.Frame(
            content_container, style="Card.TFrame", padding=0
        )  # Padding 由内部控制
        left_card.pack(
            side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 15)
        )  # 占 1/3 宽度由 expand 控制，这里简单处理为均分，后续调整

        # 左侧 Header
        left_header = ttk.Frame(left_card, style="Card.TFrame", padding=15)
        left_header.pack(fill=tk.X)
        ttk.Label(left_header, text="联系人列表", style="H2.TLabel").pack(side=tk.LEFT)

        # 搜索框
        search_frame = ttk.Frame(left_card, style="Card.TFrame", padding="15 0 15 10")
        search_frame.pack(fill=tk.X)
        self.search_entry = ttk.Entry(search_frame, font=("Segoe UI", 11))
        self.search_entry.pack(fill=tk.X)
        self.search_entry.bind("<KeyRelease>", self.search_contacts)
        # Placeholder 模拟
        self.search_entry.insert(0, "")

        # 列表区域
        list_frame = ttk.Frame(left_card, style="Card.TFrame", padding="1 0 1 1")
        list_frame.pack(fill=tk.BOTH, expand=True)

        self.contact_listbox = tk.Listbox(
            list_frame,
            selectmode=tk.SINGLE,
            font=("Segoe UI", 11),
            bg=colors["bg_card"],
            fg=colors["text_primary"],
            relief=tk.FLAT,
            bd=0,
            highlightthickness=0,
            selectbackground="#EFF6FF",
            selectforeground=colors["primary"],
            activestyle="none",
        )
        self.contact_listbox.pack(
            side=tk.LEFT, fill=tk.BOTH, expand=True, padx=15, pady=5
        )

        scrollbar = tk.Scrollbar(
            list_frame, orient=tk.VERTICAL, command=self.contact_listbox.yview
        )
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.contact_listbox.configure(yscrollcommand=scrollbar.set)
        self.contact_listbox.bind("<<ListboxSelect>>", self.on_contact_select)

        # 左侧底部按钮组
        btn_group = ttk.Frame(left_card, style="Card.TFrame", padding=15)
        btn_group.pack(fill=tk.X)
        ttk.Button(
            btn_group,
            text="🔄 刷新",
            style="Outline.TButton",
            command=self.refresh_contacts_threaded,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        ttk.Button(
            btn_group,
            text="⚙️ 规则",
            style="Outline.TButton",
            command=self.open_rule_manager,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        # 右侧：功能操作区 (Tabs 风格)
        right_panel = ttk.Frame(content_container)
        right_panel.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)  # 这里需要调整比例

        # 为了让左侧窄右侧宽，我们可以重新用 PanedWindow 或者 Grid
        # 临时方案：固定左侧宽度，或者接受均分
        # 更好的方案：
        content_container.pack_forget()  # 重新布局
        content_container.pack(fill=tk.BOTH, expand=True)

        main_pane = tk.PanedWindow(
            content_container,
            orient=tk.HORIZONTAL,
            bg=colors["bg_main"],
            sashwidth=10,
            bd=0,
        )
        main_pane.pack(fill=tk.BOTH, expand=True)

        main_pane.add(left_card, minsize=300)  # 左侧卡片放入 PanedWindow
        main_pane.add(right_panel, minsize=500)  # 右侧面板放入

        # 右侧内容 - 详情卡片
        detail_card = ttk.Frame(right_panel, style="Card.TFrame", padding=20)
        detail_card.pack(fill=tk.BOTH, expand=True, pady=(0, 15))

        # 详情 Header
        detail_header = ttk.Frame(detail_card, style="Card.TFrame")
        detail_header.pack(fill=tk.X, pady=(0, 20))
        ttk.Label(detail_header, text="联系人详情与操作", style="H2.TLabel").pack(
            side=tk.LEFT
        )

        # 详情内容 Grid
        info_grid = ttk.Frame(detail_card, style="Card.TFrame")
        info_grid.pack(fill=tk.X)

        # 第一行：昵称
        ttk.Label(
            info_grid,
            text="微信昵称",
            style="Subtitle.TLabel",
            background=colors["bg_card"],
        ).grid(row=0, column=0, sticky="w", pady=5)
        self.nickname_var = tk.StringVar(value="-")
        ttk.Label(
            info_grid,
            textvariable=self.nickname_var,
            font=("Segoe UI", 12),
            background=colors["bg_card"],
        ).grid(row=1, column=0, sticky="w", pady=(0, 15))

        # 第二行：当前备注
        ttk.Label(
            info_grid,
            text="当前备注",
            style="Subtitle.TLabel",
            background=colors["bg_card"],
        ).grid(row=0, column=1, sticky="w", pady=5, padx=20)
        self.original_remark_var = tk.StringVar(value="-")
        ttk.Label(
            info_grid,
            textvariable=self.original_remark_var,
            font=("Segoe UI", 12),
            background=colors["bg_card"],
        ).grid(row=1, column=1, sticky="w", pady=(0, 15), padx=20)

        # 第三行：修改备注 (带背景的输入框区域)
        input_bg = ttk.Frame(detail_card, style="TFrame", padding=15)  # 灰色背景区域
        input_bg.configure(style="TFrame")  # 使用默认灰色背景
        input_bg.pack(fill=tk.X, pady=10)

        ttk.Label(input_bg, text="新备注设置", font=("Segoe UI", 10, "bold")).pack(
            anchor="w", pady=(0, 5)
        )

        self.new_remark_entry = ttk.Entry(input_bg, font=("Segoe UI", 12))
        self.new_remark_entry.pack(fill=tk.X, pady=5)

        action_bar = ttk.Frame(input_bg)
        action_bar.pack(fill=tk.X, pady=(10, 0))

        self.save_button = ttk.Button(
            action_bar,
            text="💾 保存修改",
            style="Primary.TButton",
            command=self.save_remark,
            state=tk.DISABLED,
        )
        self.save_button.pack(side=tk.RIGHT)

        ttk.Button(
            action_bar, text="✕ 清空", style="Outline.TButton", command=self.clear_input
        ).pack(side=tk.RIGHT, padx=10)

        # 底部：日志卡片
        log_card = ttk.Frame(right_panel, style="Card.TFrame", padding=0)
        log_card.pack(fill=tk.BOTH, expand=True)

        log_header = ttk.Frame(log_card, style="Card.TFrame", padding="15 10")
        log_header.pack(fill=tk.X)
        ttk.Label(
            log_header,
            text="Recent Activity",
            font=("Segoe UI", 11, "bold"),
            background=colors["bg_card"],
        ).pack(side=tk.LEFT)

        self.log_text = scrolledtext.ScrolledText(
            log_card,
            wrap=tk.WORD,
            font=("Consolas", 9),
            bg=colors["bg_card"],
            fg=colors["text_secondary"],
            relief=tk.FLAT,
            bd=0,
            highlightthickness=0,
            height=8,
        )
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=15, pady=(0, 15))
        self.log_text.config(state=tk.DISABLED)

    def init_wechat(self):
        """初始化微信客户端"""
        try:
            # 使用单例获取 WeChat 实例（内置前置检查和 COM 初始化）
            self.wx = get_wechat()
            self.is_initialized = True
            self.update_status("✅ 微信客户端初始化成功！", "#52C41A")
            self.update_log("✅ 微信已连接 - 点击【刷新联系人】获取列表")

            # 只有在没有外部传入联系人列表时，才自动加载联系人
            if not self.use_external_contacts:
                self.refresh_contacts()
                self.update_log("🔄 自动加载联系人完成")
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

                imported_contacts = []
                current_name = None
                current_remark = ""

                for line in lines:
                    line = line.strip()
                    if line and not line.startswith(
                        ("=", "#", "导出时间", "联系人总数")
                    ):
                        if re.match(r"^\d+\.\s+.+", line):
                            if current_name:
                                imported_contacts.append(
                                    {
                                        "nickname": current_name,
                                        "remark": current_remark,
                                        "display_name": current_remark
                                        if current_remark
                                        else current_name,
                                    }
                                )
                            current_name = line.split(".", 1)[1].strip()
                            current_remark = ""
                        elif line.startswith("备注:") or line.startswith("原始备注:"):
                            current_remark = line.split(":", 1)[1].strip()
                        elif line and not current_name:
                            current_name = line

                if current_name:
                    imported_contacts.append(
                        {
                            "nickname": current_name,
                            "remark": current_remark,
                            "display_name": current_remark
                            if current_remark
                            else current_name,
                        }
                    )

                if imported_contacts:
                    existing_names = {c["nickname"].lower() for c in self.contacts}
                    new_count = 0
                    for c in imported_contacts:
                        if c["nickname"].lower() not in existing_names:
                            self.contacts.append(c)
                            new_count += 1

                    if new_count > 0:
                        self.filtered_contacts = self.contacts.copy()
                        self.update_contact_listbox()
                        messagebox.showinfo("成功", f"已导入 {new_count} 位新联系人")
                        self.update_log(f"📥 导入联系人：已导入 {new_count} 位新联系人")
                        # 如果有回调函数，通知主程序
                        if self.refresh_callback:
                            self.run_in_main_thread(
                                self.refresh_callback, self.contacts
                            )
                    else:
                        messagebox.showinfo("提示", "未发现新联系人")
            except Exception as e:
                messagebox.showerror("错误", f"导入失败: {e}")

    def export_contacts_to_txt(self):
        """导出联系人"""
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
                    f.write("微信联系人列表\n")
                    f.write("=" * 30 + "\n")
                    f.write(
                        f"导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                    )
                    f.write(f"联系人总数: {len(self.contacts)}\n\n")

                    for i, contact in enumerate(self.contacts, 1):
                        f.write(f"{i}. {contact['nickname']}\n")
                        f.write(f"   备注: {contact['remark']}\n")
                        f.write("-" * 20 + "\n")

                messagebox.showinfo("成功", f"已导出 {len(self.contacts)} 位联系人")
                self.update_log(f"📤 联系人已导出到: {path}")
            except Exception as e:
                messagebox.showerror("错误", f"导出失败: {e}")

    def _update_log_impl(self, msg):
        """实际更新日志的实现"""
        if not hasattr(self, "log_text"):
            return

        self.log_text.config(state=tk.NORMAL)
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_line = f"[{timestamp}] {msg}\n"

        self.log_text.insert(tk.END, log_line)
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)
        logger.info(msg)

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
        """刷新联系人列表（支持同步获取标签）"""
        try:
            self.update_status("🔄 正在获取联系人列表...", "#5B8FF9")
            self.update_log("🔄 开始读取微信联系人...")

            # 获取所有联系人详情
            # 注意：GetFriendDetails 可能返回不包含标签的字典，具体取决于wxauto版本
            # 尝试调用 GetFriendDetails
            all_friends = self.wx.GetFriendDetails(timeout=20000)
            self.update_log(f"📥 从微信获取到 {len(all_friends)} 条联系人数据")

            self.contacts = []
            for friend in all_friends:
                if isinstance(friend, dict):
                    # 适配不同wxauto版本的字段名
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
                    # 尝试获取标签
                    tags = (
                        friend.get("标签", "")
                        or friend.get("tags", "")
                        or friend.get("Tags", "")
                        or ""
                    )
                    if isinstance(tags, list):
                        tags = ",".join(tags)

                    # 优先用备注，没有备注用昵称
                    display_name = remark if remark else nickname

                    self.contacts.append(
                        {
                            "nickname": nickname,
                            "remark": remark,
                            "tags": tags,  # 保存标签信息
                            "display_name": display_name,
                            "original_info": friend,
                        }
                    )

            # 按显示名称排序
            self.contacts.sort(key=lambda x: x["display_name"])
            self.filtered_contacts = self.contacts.copy()

            # 更新联系人列表
            self.run_in_main_thread(self.update_contact_listbox)

            # 状态和日志更新
            self.update_status(
                f"✅ 联系人获取完成！共找到 {len(self.contacts)} 位联系人", "#52C41A"
            )
            self.update_log(f"✅ 联系人加载完成 - 共 {len(self.contacts)} 位联系人")

            # 调用回调函数，通知主程序更新联系人
            if self.refresh_callback:
                self.run_in_main_thread(self.refresh_callback, self.contacts)

        except Exception as e:
            error_msg = str(e)
            self.update_status(f"❌ 获取联系人失败: {error_msg}", "#FF4D4F")
            self.update_log(f"❌ 联系人读取失败: {error_msg}\n{traceback.format_exc()}")
            self.run_in_main_thread(
                lambda: messagebox.showerror(
                    "错误",
                    f"获取联系人失败: {error_msg}\n\n请确保：\n1. wxauto已安装且版本兼容\n2. 微信PC端已登录且处于前台",
                )
            )
        finally:
            self.is_refreshing = False

    def update_contact_listbox(self):
        """更新联系人列表框"""
        self.contact_listbox.delete(0, tk.END)
        for contact in self.filtered_contacts:
            # 适配不同的联系人结构
            if "display_name" in contact:
                display_text = contact["display_name"]
            elif "original_display" in contact:
                display_text = contact["original_display"]
            else:
                display_text = contact.get("name", "") or contact.get("nickname", "")

            # 适配不同的联系人结构
            remark = contact.get("remark", "")
            nickname = contact.get("nickname", "")
            tags = contact.get("tags", "")

            # 组装显示文本
            if remark and nickname and remark != nickname:
                display_text += f" (昵称: {nickname})"

            # 如果有标签，显示标签
            if tags:
                display_text += f" [🏷️{tags}]"

            self.contact_listbox.insert(tk.END, display_text)

        # 更新统计数据
        if hasattr(self, "metric_total_var"):
            self.metric_total_var.set(str(len(self.filtered_contacts)))

    def search_contacts(self, event):
        """搜索联系人"""
        keyword = self.search_entry.get().strip().lower()
        if not keyword:
            self.filtered_contacts = self.contacts.copy()
        else:
            self.filtered_contacts = []
            for c in self.contacts:
                # 适配不同的联系人结构，提取可搜索的字段
                search_fields = []
                search_fields.append(c.get("nickname", "").lower())
                search_fields.append(c.get("remark", "").lower())

                # 检查是否有display_name或original_display字段
                if "display_name" in c:
                    search_fields.append(c["display_name"].lower())
                elif "original_display" in c:
                    search_fields.append(c["original_display"].lower())
                else:
                    # 添加其他可能的名称字段
                    search_fields.append(c.get("name", "").lower())

                # 检查是否有匹配的关键词
                if any(keyword in field for field in search_fields):
                    self.filtered_contacts.append(c)

        self.update_contact_listbox()
        self.status_label.config(
            fg="#5B8FF9", text=f"🔍 搜索到 {len(self.filtered_contacts)} 位匹配的联系人"
        )

    def on_contact_select(self, event):
        """联系人选择事件"""
        selection = self.contact_listbox.curselection()
        if selection:
            index = selection[0]
            self.selected_contact = self.filtered_contacts[index]
            # 更新联系人信息显示
            self.nickname_var.set(self.selected_contact["nickname"])
            self.original_remark_var.set(self.selected_contact["remark"])
            self.new_remark_entry.delete(0, tk.END)
            self.new_remark_entry.insert(0, self.selected_contact["remark"])
            # 启用保存按钮
            self.save_button.config(state=tk.NORMAL)
        else:
            self.selected_contact = None
            self.save_button.config(state=tk.DISABLED)

    def clear_input(self):
        """清空输入"""
        self.new_remark_entry.delete(0, tk.END)

    def cancel_selection(self):
        """取消选择"""
        self.contact_listbox.selection_clear(0, tk.END)
        self.selected_contact = None
        self.nickname_var.set("未选择联系人")
        self.original_remark_var.set("")
        self.new_remark_entry.delete(0, tk.END)
        self.save_button.config(state=tk.DISABLED)

    def save_remark(self):
        """保存备注修改 - 主线程"""
        if not self.selected_contact:
            messagebox.showwarning("警告", "请先选择一个联系人！")
            return

        new_remark = self.new_remark_entry.get().strip()
        if not new_remark:
            messagebox.showwarning("警告", "新备注不能为空！")
            return

        if new_remark == self.selected_contact["remark"]:
            messagebox.showinfo("提示", "新备注与当前备注相同，无需修改！")
            return

        # 适配不同的联系人结构，获取显示名称
        if "display_name" in self.selected_contact:
            contact_display_name = self.selected_contact["display_name"]
        elif "original_display" in self.selected_contact:
            contact_display_name = self.selected_contact["original_display"]
        else:
            contact_display_name = self.selected_contact.get(
                "name", ""
            ) or self.selected_contact.get("nickname", "")

        # 确认修改
        if not messagebox.askyesno(
            "确认",
            f"确定要将联系人 '{contact_display_name}' 的备注修改为 '{new_remark}' 吗？",
        ):
            return

        # 禁用保存按钮，防止重复点击
        self.save_button.config(state=tk.DISABLED)

        # 更新状态
        self.status_label.config(fg="#5B8FF9", text="🔄 正在修改联系人备注...")
        self.update_log(f"🔄 开始修改联系人 '{contact_display_name}' 的备注")

        # 创建子线程处理耗时操作
        threading.Thread(
            target=self.save_remark_thread,
            args=(contact_display_name, new_remark),
            daemon=True,
        ).start()

    def save_remark_thread(self, contact_display_name, new_remark):
        """保存备注修改 - 子线程"""
        try:
            # 1. 打开联系人聊天窗口
            # self.update_log(f"🔄 正在打开联系人'{contact_display_name}'的聊天窗口...")
            self.wx.ChatWith(contact_display_name)
            time.sleep(0.5)

            # 2. 调用wxauto的ManageFriend方法修改备注
            # self.update_log(f"🔄 正在修改备注...")

            # wxauto的ManageFriend没有返回值，它直接操作UI
            self.wx.ManageFriend(remark=new_remark)

            # 3. 假设修改成功（因为无法直接获取返回值）
            # self.update_log(f"✅ 备注修改指令已发送")

            # 4. 更新本地数据
            self.selected_contact["remark"] = new_remark

            # 适配不同的联系人结构，更新显示名称
            if "display_name" in self.selected_contact:
                self.selected_contact["display_name"] = (
                    new_remark if new_remark else self.selected_contact["nickname"]
                )
            elif "original_display" in self.selected_contact:
                self.selected_contact["original_display"] = (
                    new_remark if new_remark else self.selected_contact["name"]
                )

            # 5. 在主线程更新界面
            self.run_in_main_thread(self._update_ui_after_save, new_remark)

        except Exception as e:
            self.run_in_main_thread(self._show_error_after_save, str(e))

    def _update_ui_after_save(self, new_remark):
        """保存成功后更新界面"""
        self.original_remark_var.set(new_remark)
        self.update_contact_listbox()
        self.status_label.config(fg="#52C41A", text="✅ 备注修改成功！")
        self.update_log(f"✅ 成功修改备注为: {new_remark}")
        self.save_button.config(state=tk.NORMAL)
        messagebox.showinfo("成功", "备注修改成功！")

    def _show_error_after_save(self, error_msg):
        """保存失败后显示错误"""
        self.status_label.config(fg="#FF4D4F", text="❌ 备注修改失败！")
        self.update_log(f"❌ 备注修改失败: {error_msg}")
        self.save_button.config(state=tk.NORMAL)
        messagebox.showerror("错误", f"备注修改失败: {error_msg}")

    def run(self):
        """运行窗口"""
        self.root.mainloop()

