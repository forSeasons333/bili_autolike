# main_gui.py
# -*- coding: utf-8 -*-

# --- 基础模块导入 ---
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from tkinter import font as tkFont
from tkinter import PhotoImage
import requests
import time
import random
import json
import threading
import queue
from PIL import Image, ImageTk
import traceback
import webbrowser
import sys
import os

# --- 自定义模块导入 ---
from login import login_via_qrcode # 导入登录逻辑

# --- 界面颜色主题定义 (浅色清爽主题) ---
BG_LIGHT_PRIMARY = "#F5F5F5"    # 主背景色 (浅灰)
BG_WIDGET_ALT = "#FFFFFF"       # 部件背景色 (白)
FG_TEXT_DARK = "#212121"      # 主要文字颜色 (深灰)
FG_TEXT_MUTED = "#616161"      # 次要文字颜色 (中灰)
ACCENT_BRIGHT_BLUE = "#2979FF"  # 强调色 (亮蓝)
BORDER_LIGHT = "#E0E0E0"       # 边框颜色 (浅灰)
BUTTON_FG = "#FFFFFF"          # 按钮文字颜色 (白)
BUTTON_ACTIVE_BLUE = "#0D47A1"  # 按钮按下颜色 (深蓝)
STATUS_BG = "#EEEEEE"         # 状态栏背景色 (稍深灰)
STATUS_FG = FG_TEXT_DARK        # 状态栏文字颜色
ERROR_FG = "#D32F2F"         # 错误提示颜色 (红)
SUCCESS_FG = "#388E3C"       # 成功提示颜色 (绿)

# --- Bilibili API 相关定义 ---
DYNAMICS_FETCH_URL_BASE = "https://api.vc.bilibili.com/dynamic_svr/v1/dynamic_svr/space_history?need_top=1&platform=web&ps=20"
LIKE_DYNAMIC_URL = "https://api.vc.bilibili.com/dynamic_like/v1/dynamic_like/thumb"
HEADERS = { # 通用请求头
    'Accept': 'application/json, text/plain, */*', 'Accept-Encoding': 'gzip, deflate, br',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8', 'Origin': 'https://space.bilibili.com',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36'
}
# --- Brotli 库检查 ---
try: import brotli # 检查是否有Brotli解压支持
except ImportError: print("警告：未找到 'brotli' 库，建议运行: pip install brotli")

# --- 全局日志记录辅助函数 ---
def _log_message(log_queue, message):
    """将日志消息放入队列，以便GUI线程安全地显示。"""
    if log_queue:
        try: log_queue.put(f"[{time.strftime('%H:%M:%S')}] {str(message)}")
        except Exception as e: print(f"[{time.strftime('%H:%M:%S')}] {str(message)}"); print(f"Queue Error: {e}")
    else: print(f"[{time.strftime('%H:%M:%S')}] {str(message)}") # 如果没有队列，直接打印到控制台

# --- 资源路径辅助函数 ---
def resource_path(relative_path):
    """ 获取资源的绝对路径，兼容开发环境和PyInstaller打包环境 """
    try:
        # PyInstaller打包后会创建临时目录，路径存储在sys._MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        # 在开发环境中运行
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# --- 后台网络请求与逻辑函数 ---

def get_up_dynamics(session, host_uid, offset_dynamic_id, log_queue, stop_event):
    """
    获取指定UP主的动态列表 (使用 space_history API)。
    支持重试，并将日志发送到队列，同时检查停止信号。
    返回: (动态数据列表, 下一页偏移ID, 是否还有更多) 或 (None, None, None) 如果失败。
    """
    url = f"{DYNAMICS_FETCH_URL_BASE}&host_uid={host_uid}&offset_dynamic_id={offset_dynamic_id}"
    dynamic_headers = HEADERS.copy()
    dynamic_headers['Referer'] = f'https://space.bilibili.com/{host_uid}/dynamic' # 设置Referer
    max_retries=3; initial_retry_delay=5; retries = 0; retry_delay = initial_retry_delay

    while retries <= max_retries:
        if stop_event.is_set(): return None, None, None # 检查停止信号
        try:
            response = session.get(url, headers=dynamic_headers, timeout=25)
            response.raise_for_status() # 检查HTTP错误
            if stop_event.is_set(): return None, None, None # 请求后再次检查

            try: data = response.json() # 解析JSON
            except json.JSONDecodeError: # 处理JSON解析错误
                _log_message(log_queue, f"错误: 获取动态列表JSON解析失败 (Offset: {offset_dynamic_id})")
                if response.headers.get('Content-Encoding') == 'br': _log_message(log_queue, "提示：检查 'brotli' 库。")
                if retries < max_retries: _log_message(log_queue, f"将在 {retry_delay:.1f} 秒后重试..."); time.sleep(retry_delay); retries += 1; retry_delay *= 1.5; continue
                else: _log_message(log_queue, "获取动态JSON错误达到最大重试次数。"); return None, None, None

            api_code = data.get("code"); api_message = data.get("message", "")
            if api_code == 0: # API请求成功
                dynamics_data=data.get("data", {}); cards=dynamics_data.get("cards", [])
                has_more = dynamics_data.get("has_more", 0) == 1; next_offset_from_api = str(dynamics_data.get("next_offset", "0"))
                extracted_list = []; last_processed_dynamic_id = None
                for card in cards: # 遍历动态卡片
                    if stop_event.is_set(): return None, None, None # 循环内检查停止
                    desc=card.get('desc');
                    if not desc: continue
                    dynamic_id = str(desc.get('dynamic_id_str', desc.get('dynamic_id', '')))
                    if not dynamic_id or dynamic_id == "0": continue
                    last_processed_dynamic_id = dynamic_id
                    # 判断点赞状态
                    like_state=desc.get('like_state'); is_liked_field=desc.get('is_liked'); effective_like_status = 0
                    if isinstance(like_state, int): effective_like_status = like_state
                    elif isinstance(is_liked_field, int): effective_like_status = is_liked_field
                    # 提取需要的信息
                    item_data = { "dynamic_id": dynamic_id, "needs_like": (effective_like_status == 0), "desc_text": f"Type {desc.get('type', '?')} User {desc.get('user_profile',{}).get('info',{}).get('uname','?')}"[:50]}
                    extracted_list.append(item_data)
                # 计算下一页的offset
                next_request_offset = next_offset_from_api
                if last_processed_dynamic_id: next_request_offset = last_processed_dynamic_id
                if next_request_offset == offset_dynamic_id and offset_dynamic_id != "0": has_more = False # 检查offset是否卡住
                return extracted_list, next_request_offset, has_more # 返回结果

            # 处理API返回的错误码
            is_rate_limited = ("频繁" in api_message or api_code in [-799, -412, -509, 4128002])
            if is_rate_limited and retries < max_retries: # 如果是频率限制且可重试
                _log_message(log_queue, f"获取动态列表API限制 (code={api_code})，稍后重试..."); time.sleep(retry_delay); retries += 1; retry_delay *= 1.5; continue
            else: # 其他API错误或达到重试上限
                _log_message(log_queue, f"获取动态列表失败: code={api_code}, msg='{api_message}'");
                if is_rate_limited: _log_message(log_queue, f"已达最大重试次数({max_retries})。")
                if api_code == -101: _log_message(log_queue, "错误: 登录状态失效。"); raise RuntimeError("登录失效(fetch)") # 抛出严重错误
                return None, None, None # 返回失败

        # 处理网络或请求异常
        except (requests.exceptions.Timeout, requests.exceptions.RequestException) as e: _log_message(log_queue, f"获取动态网络错误: {e}");
        except RuntimeError as e: raise e # 重新抛出由内部逻辑触发的严重错误
        except Exception as e: _log_message(log_queue, f"获取动态未知错误: {e}"); traceback.print_exc(); return None, None, None

        # 如果发生异常且未达到重试上限，则进行重试
        if retries < max_retries:
            _log_message(log_queue, f"获取动态出错，{retry_delay:.1f}秒后重试..."); time.sleep(retry_delay); retries += 1; retry_delay *= 1.5; continue
        else: # 达到重试上限
             _log_message(log_queue, "获取动态达到最大重试次数。"); return None, None, None

    return None, None, None # 循环结束仍未成功

def like_dynamic(session, dynamic_id, csrf_token, log_queue, stop_event):
    """
    为指定ID的动态点赞 (使用 thumb API)。
    支持重试，并将日志发送到队列，同时检查停止信号。
    返回: True 如果成功或已点赞，False 如果失败。
    """
    payload = { "dynamic_id": dynamic_id, "up": 1, "csrf": csrf_token }
    dynamic_headers = HEADERS.copy(); dynamic_headers['Referer'] = f'https://t.bilibili.com/{dynamic_id}'; dynamic_headers['Origin'] = 'https://t.bilibili.com'
    max_like_attempts=3; current_attempt=0; base_like_delay=1.5

    while current_attempt < max_like_attempts:
        if stop_event.is_set(): return False # 检查停止信号
        current_attempt += 1
        if current_attempt > 1: # 重试前等待
            if stop_event.is_set(): return False
            retry_like_delay = (2**(current_attempt-2)) * base_like_delay + random.uniform(0.1, 0.3); time.sleep(retry_like_delay)
        if stop_event.is_set(): return False

        response = None
        try:
            response = session.post(LIKE_DYNAMIC_URL, data=payload, headers=dynamic_headers, timeout=15)
            if stop_event.is_set(): return False
            response.raise_for_status() # 检查HTTP错误

            try: # 解析点赞响应
                data = response.json()
                api_code = data.get("code"); api_message = data.get("message","")
                if api_code == 0: _log_message(log_queue, f"点赞成功: ID={dynamic_id}"); return True
                elif api_code == 71000: _log_message(log_queue, f"已点赞过: ID={dynamic_id}"); return True
                elif api_code in [-412, -509, 4128002] or "频繁" in api_message: _log_message(log_queue, f"点赞速率限制: ID={dynamic_id}, code={api_code}"); continue # 重试
                elif api_code == -111: _log_message(log_queue, f"错误: CSRF校验失败: ID={dynamic_id}"); raise RuntimeError("CSRF失效(like)") # 严重错误
                elif api_code == -101: _log_message(log_queue, f"错误: 账号未登录: ID={dynamic_id}"); raise RuntimeError("登录失效(like)") # 严重错误
                elif api_code == -400: _log_message(log_queue, f"错误: 无效请求 (ID={dynamic_id})。"); return False # 无效请求不重试
                else: _log_message(log_queue, f"点赞未知API错误: ID={dynamic_id}, code={api_code}"); continue # 其他API错误尝试重试
            except json.JSONDecodeError: # 处理JSON解析失败
                _log_message(log_queue, f"错误: 点赞响应JSON解析失败: ID={dynamic_id}")
                if response.headers.get('Content-Encoding') == 'br': _log_message(log_queue, "提示: 检查 'brotli' 库。")
                continue # 重试

        # 处理网络或请求异常
        except requests.exceptions.Timeout: _log_message(log_queue, f"点赞超时: ID={dynamic_id}"); continue
        except requests.exceptions.RequestException as e:
            status_code = response.status_code if response else "N/A"; _log_message(log_queue, f"点赞网络/HTTP失败: ID={dynamic_id}, Status={status_code}, Err:{e}")
            if status_code in [401, 403]: _log_message(log_queue, f"错误：HTTP {status_code}，登录/权限问题。"); raise RuntimeError(f"HTTP {status_code}(like)") # 严重错误
            continue # 其他网络/HTTP错误尝试重试
        except RuntimeError as e: raise e # 重新抛出严重错误
        except Exception as e: _log_message(log_queue, f"点赞意外错误: ID={dynamic_id}, Err:{e}"); traceback.print_exc(); return False # 未知错误，不再重试

    _log_message(log_queue, f"点赞 ID {dynamic_id} 重试多次后失败。")
    return False


# --- 图形用户界面主类 ---
class BiliLikerApp:
    def __init__(self, root):
        """初始化应用程序"""
        self.root = root
        self.root.title("动态守护姬DynamicGuardian v1.0.1") # 设置窗口标题
        self.root.geometry("650x550")         # 设置初始窗口大小
        self.root.minsize(600, 500)          # 设置最小窗口大小
        self.root.config(bg=BG_LIGHT_PRIMARY)  # 设置主背景色

        # --- 设置程序图标 ---
        self._app_icon = None # 用于保持对图标对象的引用
        try:
            icon_path = resource_path('app_icon.png') # 使用辅助函数获取路径
            if os.path.exists(icon_path): # 检查文件是否存在
                icon_image = PhotoImage(file=icon_path) # 加载PNG图标
                self.root.iconphoto(False, icon_image) # 设置窗口图标
                self._app_icon = icon_image # 保存引用，防止被垃圾回收
                print(f"成功加载并设置图标: {icon_path}")
            else: print(f"警告：图标文件未找到: '{icon_path}'")
        except Exception as e: print(f"设置图标失败: {e}"); traceback.print_exc()

        # --- 初始化状态变量 ---
        self.cookies = None             # 存储登录后的 Cookies
        self.csrf_token = None          # 存储 CSRF Token (bili_jct)
        self.session = None             # requests 的 Session 对象
        self.is_logged_in = False       # 当前是否已登录
        self.is_running = False         # 后台任务是否正在运行
        self.backend_thread = None      # 后台任务线程对象
        self.stop_event = threading.Event() # 用于通知后台线程停止的事件
        self.log_queue = queue.Queue()  # 用于后台线程与GUI线程通信的队列
        self.qr_window = None           # 二维码弹出窗口对象
        self.login_stop_event = threading.Event() # 用于单独停止登录过程的事件
        self._qr_tk_image_ref = None    # 用于保持对二维码PhotoImage的引用

        # --- 初始化字体 ---
        self.default_font = tkFont.Font(family="Microsoft YaHei UI", size=10)
        self.label_font = tkFont.Font(family="Microsoft YaHei UI", size=10)
        self.button_font = tkFont.Font(family="Microsoft YaHei UI", size=10, weight='bold')
        self.entry_font = tkFont.Font(family="Microsoft YaHei UI", size=10)
        self.label_frame_font = tkFont.Font(family="Microsoft YaHei UI", size=10, weight="bold")
        self.log_font = tkFont.Font(family="Microsoft YaHei UI", size=9)

        # --- 初始化并应用界面样式 ---
        self.style = ttk.Style()
        try: self.style.theme_use('clam') # 使用 'clam' 主题作为基础
        except tk.TclError: print("Clam theme not available.")
        self._apply_styles() # 应用自定义样式

        # --- 创建界面控件 ---
        self._create_widgets()
        self._create_menu() # 创建菜单栏

        # --- 启动日志队列检查循环 ---
        self.root.after(100, self._check_log_queue) # 100ms后开始检查队列

        # --- 绑定窗口关闭事件 ---
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing) # 点击关闭按钮时调用 _on_closing

    def _apply_styles(self):
        """配置ttk部件的样式"""
        # 配置全局默认样式
        self.style.configure('.', background=BG_LIGHT_PRIMARY, foreground=FG_TEXT_DARK, font=self.default_font, borderwidth=1)
        # 配置Frame样式
        self.style.configure('TFrame', background=BG_LIGHT_PRIMARY)
        # 配置Label样式
        self.style.configure('TLabel', background=BG_LIGHT_PRIMARY, foreground=FG_TEXT_DARK, font=self.label_font)
        # 配置LabelFrame样式
        self.style.configure('TLabelframe', background=BG_LIGHT_PRIMARY, bordercolor=BORDER_LIGHT, borderwidth=1, relief=tk.GROOVE)
        self.style.configure('TLabelframe.Label', background=BG_LIGHT_PRIMARY, foreground=ACCENT_BRIGHT_BLUE, font=self.label_frame_font)
        # 配置Button样式
        self.style.configure('TButton', background=ACCENT_BRIGHT_BLUE, foreground=BUTTON_FG, bordercolor=ACCENT_BRIGHT_BLUE, borderwidth=0, padding=(10, 5), relief=tk.FLAT, font=self.button_font)
        self.style.map('TButton', background=[('pressed', BUTTON_ACTIVE_BLUE), ('active', ACCENT_BRIGHT_BLUE), ('disabled', BORDER_LIGHT)], foreground=[('disabled', FG_TEXT_MUTED)], relief=[('pressed', tk.FLAT)])
        # 配置Entry样式
        self.style.configure('TEntry', fieldbackground=BG_WIDGET_ALT, foreground=FG_TEXT_DARK, insertcolor=FG_TEXT_DARK, bordercolor=BORDER_LIGHT, borderwidth=1, relief=tk.SOLID, font=self.entry_font)
        self.style.map('TEntry', bordercolor=[('focus', ACCENT_BRIGHT_BLUE)], fieldbackground=[('disabled', BG_LIGHT_PRIMARY)], foreground=[('disabled', FG_TEXT_MUTED)])
        # 配置状态栏Label样式
        self.style.configure('Status.TLabel', background=STATUS_BG, foreground=STATUS_FG, padding=(5, 3), font=self.default_font)
        # 配置停止按钮样式
        self.style.configure('Stop.TButton', background=ERROR_FG, foreground=BUTTON_FG) # 红色背景
        self.style.map('Stop.TButton', background=[('pressed', '#B71C1C'), ('active', ERROR_FG), ('disabled', BORDER_LIGHT)]) # 按下/悬停/禁用状态

    def _create_menu(self):
        """创建顶部菜单栏"""
        menubar = tk.Menu(self.root) # 创建菜单栏实例
        self.root.config(menu=menubar) # 将菜单栏添加到主窗口

        # 创建 "帮助" 菜单
        help_menu = tk.Menu(menubar, tearoff=0, font=self.default_font) # 创建子菜单，禁止分离
        menubar.add_cascade(label="帮助(H)", menu=help_menu) # 将 "帮助" 菜单添加到菜单栏

        # 添加 "关于" 菜单项
        help_menu.add_command(label="关于(A)...", command=self._show_about_window, font=self.default_font)

    def _show_about_window(self):
        """显示'关于'信息窗口"""
        about_window = tk.Toplevel(self.root) # 创建顶层窗口
        about_window.withdraw() # 先隐藏，防止闪烁
        about_window.title("关于 动态守护姬DynamicGuardian") # 设置标题
        about_window.resizable(False, False) # 禁止调整大小
        about_window.transient(self.root) # 设置为root的瞬态窗口
        about_window.grab_set() # 设置为模态，阻止与其他窗口交互
        about_window.focus_set() # 获取焦点
        about_window.config(bg=BG_LIGHT_PRIMARY) # 设置背景色

        # 尝试设置'关于'窗口的图标 (如果主窗口图标加载成功)
        if self._app_icon:
            try: about_window.iconphoto(False, self._app_icon)
            except Exception as e: print(f"设置'关于'窗口图标失败: {e}")

        # 创建内容框架
        content_frame = ttk.Frame(about_window, padding="15", style='TFrame'); content_frame.pack(expand=True, fill=tk.BOTH)

        # 显示程序标题
        title_font = tkFont.Font(family="Microsoft YaHei UI", size=12, weight="bold")
        ttk.Label(content_frame, text="动态守护姬DynamicGuardian", font=title_font, style='TLabel').pack(pady=(0, 10))
        # 显示版本号
        ttk.Label(content_frame, text="版本: 1.0.1", style='TLabel').pack(pady=2)
        # 显示作者信息
        ttk.Label(content_frame, text="作者: 四季Destination", style='TLabel').pack(pady=2)

        # 添加超链接 (使用普通 tk.Label 实现)
        link_font = tkFont.Font(family="Microsoft YaHei UI", size=10, underline=True)
        # GitHub 链接
        github_link = tk.Label(content_frame, text="访问 GitHub 主页", fg=ACCENT_BRIGHT_BLUE, cursor="hand2", font=link_font, bg=BG_LIGHT_PRIMARY); github_link.pack(pady=5)
        github_link.bind("<Button-1>", lambda e: webbrowser.open_new_tab("https://github.com/forSeasons333"))
        # Bilibili 主页链接
        bili_link = tk.Label(content_frame, text="访问 Bilibili 主页", fg=ACCENT_BRIGHT_BLUE, cursor="hand2", font=link_font, bg=BG_LIGHT_PRIMARY); bili_link.pack(pady=5)
        bili_link.bind("<Button-1>", lambda e: webbrowser.open_new_tab("https://space.bilibili.com/403039446"))

        # 添加确定按钮
        ok_button = ttk.Button(content_frame, text="确定", command=about_window.destroy, width=10, style='TButton'); ok_button.pack(pady=(15, 0))

        # 计算窗口大小和居中位置
        about_window.update_idletasks() # 更新以获取部件大小
        win_w = about_window.winfo_width()
        win_h = about_window.winfo_height()
        min_width = 420 # 设置最小宽度
        if win_w < min_width: win_w = min_width
        main_x = self.root.winfo_rootx(); main_y = self.root.winfo_rooty()
        main_w = self.root.winfo_width(); main_h = self.root.winfo_height()
        x_pos = main_x + (main_w // 2) - (win_w // 2)
        y_pos = main_y + (main_h // 2) - (win_h // 2)

        # 设置最终位置并显示窗口
        about_window.geometry(f"{win_w}x{win_h}+{max(0, x_pos)}+{max(0, y_pos)}")
        about_window.deiconify() # 显示窗口
        ok_button.focus_set() # 让确定按钮获得焦点

    def _create_widgets(self):
        """创建主界面的所有控件"""
        # 控制区框架
        control_frame = ttk.Frame(self.root, padding="10", style='TFrame'); control_frame.pack(fill=tk.X, side=tk.TOP, anchor=tk.N)

        # 登录区
        login_frame = ttk.LabelFrame(control_frame, text="登录", padding="5", style='TLabelframe'); login_frame.pack(fill=tk.X, pady=5)
        self.login_status_label = ttk.Label(login_frame, text="状态: 未登录", width=20, foreground=FG_TEXT_MUTED, style='TLabel'); self.login_status_label.pack(side=tk.LEFT, padx=(5, 10), pady=5)
        self.login_button = ttk.Button(login_frame, text="扫码登录", command=self._start_login, width=12, style='TButton'); self.login_button.pack(side=tk.LEFT, padx=5, pady=5)

        # 配置区
        config_frame = ttk.LabelFrame(control_frame, text="配置", padding="10", style='TLabelframe'); config_frame.pack(fill=tk.X, pady=5); config_frame.columnconfigure(1, weight=1); config_frame.columnconfigure(3, weight=1)
        ttk.Label(config_frame, text="目标UID:", style='TLabel').grid(row=0, column=0, padx=5, pady=3, sticky=tk.W)
        self.uid_entry = ttk.Entry(config_frame, width=15, style='TEntry'); self.uid_entry.grid(row=0, column=1, padx=5, pady=3, sticky=tk.W)
        ttk.Label(config_frame, text="初始点赞数:", style='TLabel').grid(row=0, column=2, padx=5, pady=3, sticky=tk.W)
        self.max_likes_entry = ttk.Entry(config_frame, width=10, style='TEntry'); self.max_likes_entry.grid(row=0, column=3, padx=5, pady=3, sticky=tk.W); self.max_likes_entry.insert(0, "30")
        ttk.Label(config_frame, text="监控间隔(秒):", style='TLabel').grid(row=1, column=0, padx=5, pady=3, sticky=tk.W) # 标签改为秒
        self.interval_entry = ttk.Entry(config_frame, width=10, style='TEntry'); self.interval_entry.grid(row=1, column=1, padx=5, pady=3, sticky=tk.W); self.interval_entry.insert(0, "60") # 默认值改为秒

        # 操作按钮区
        action_frame = ttk.Frame(control_frame, style='TFrame'); action_frame.pack(pady=10)
        self.action_button = ttk.Button(action_frame, text="启动任务", command=self._start_stop_liking, state=tk.DISABLED, width=15, style='TButton'); self.action_button.pack()

        # 日志区
        log_frame = ttk.LabelFrame(self.root, text="日志", padding="5", style='TLabelframe'); log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 5))
        self.log_text = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD, width=70, height=18, state=tk.DISABLED, bd=1, relief=tk.SOLID, bg=BG_WIDGET_ALT, fg=FG_TEXT_DARK, insertbackground=FG_TEXT_DARK, font=self.log_font, borderwidth=1, highlightthickness=1, highlightcolor=ACCENT_BRIGHT_BLUE, highlightbackground=BORDER_LIGHT); self.log_text.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

        # 状态栏
        self.status_bar = ttk.Label(self.root, text="准备就绪", relief=tk.FLAT, anchor=tk.W, style='Status.TLabel', borderwidth=0); self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)

    def _log_to_gui(self, message):
        """将消息添加到GUI日志文本区域"""
        try:
            self.log_text.config(state=tk.NORMAL) # 允许编辑
            self.log_text.insert(tk.END, str(message) + "\n") # 插入消息
            self.log_text.see(tk.END) # 滚动到底部
            self.log_text.config(state=tk.DISABLED) # 禁止编辑
        except tk.TclError as e: print(f"GUI Log Error (widget likely destroyed): {e}") # 处理窗口已销毁的错误
        except Exception as e: print(f"Unexpected GUI Log Error: {e}") # 处理其他异常

    def _check_log_queue(self):
        """定时检查后台线程发送的消息队列，并更新GUI状态"""
        try:
            while not self.log_queue.empty(): # 处理所有当前队列中的消息
                message = self.log_queue.get_nowait() # 非阻塞获取
                # --- 处理特殊控制消息 ---
                if message == "LOGIN_SUCCESS":
                    self.is_logged_in = True; self.login_status_label.config(text="状态: 已登录", foreground=SUCCESS_FG)
                    self.action_button.config(state=tk.NORMAL); self.login_button.config(state=tk.DISABLED)
                    self._close_qr_window(); self.status_bar.config(text="登录成功，配置后可启动任务。")
                elif message == "LOGIN_FAILED":
                    self.is_logged_in = False; self.login_status_label.config(text="状态: 登录失败", foreground=ERROR_FG)
                    self.action_button.config(state=tk.DISABLED); self.login_button.config(state=tk.NORMAL)
                    self._close_qr_window(); self.status_bar.config(text="登录失败，请重试。")
                elif message == "LOGIN_PROCESS_FINISHED":
                     # 如果登录未成功（例如超时或取消），确保登录按钮可用
                     if not self.is_logged_in: self.login_button.config(state=tk.NORMAL); self.login_status_label.config(text="状态: 未登录", foreground=FG_TEXT_MUTED)
                     self._close_qr_window()
                elif message == "BACKEND_STARTED":
                    # 后台任务已启动
                    self.is_running = True; self.action_button.config(text="中止任务", style='Stop.TButton', state=tk.NORMAL) # 启用中止按钮
                    self.status_bar.config(text="运行中..."); self._set_config_state(tk.DISABLED) # 禁用配置
                elif message == "BACKEND_STOPPED_MANUAL" or message == "BACKEND_STOPPED_ERROR":
                    # 后台任务已停止（手动或错误）
                    self.is_running = False; self.action_button.config(text="启动任务", style='TButton', state=tk.NORMAL if self.is_logged_in else tk.DISABLED) # 重置按钮
                    self.status_bar.config(text="已停止" if message == "BACKEND_STOPPED_MANUAL" else "错误停止，请检查日志。")
                    self._set_config_state(tk.NORMAL); self.stop_event.clear() # 启用配置，重置停止事件
                # --- 处理普通日志消息 ---
                else: self._log_to_gui(message)
        except queue.Empty: pass # 队列为空，正常
        except Exception as e: self._log_to_gui(f"处理日志队列时出错: {e}"); traceback.print_exc() # 记录处理队列时的异常

        # 再次调度此方法以继续检查队列
        self.root.after(150, self._check_log_queue) # 150毫秒后再次检查

    def _display_qr_code_window(self, qr_pil_image):
        """通过主线程显示二维码窗口"""
        self.root.after(0, self._create_qr_window_in_main_thread, qr_pil_image)

    def _create_qr_window_in_main_thread(self, qr_pil_image):
        """在主GUI线程中创建或更新二维码窗口"""
        if self.qr_window and self.qr_window.winfo_exists(): self._close_qr_window() # 关闭旧窗口
        try:
            self.qr_window = tk.Toplevel(self.root); self.qr_window.title("扫描二维码登录"); self.qr_window.resizable(False, False)
            self.qr_window.transient(self.root); self.qr_window.attributes("-topmost", True)
            self.qr_window.protocol("WM_DELETE_WINDOW", self._cancel_login); self.qr_window.config(bg=BG_WIDGET_ALT)
            # 尝试设置二维码窗口图标
            if self._app_icon:
                try: self.qr_window.iconphoto(False, self._app_icon)
                except Exception as e: print(f"设置QR窗口图标失败: {e}")
            self._qr_tk_image_ref = ImageTk.PhotoImage(qr_pil_image) # 转换图片格式并保存引用
            qr_label = tk.Label(self.qr_window, image=self._qr_tk_image_ref, bg=BG_WIDGET_ALT, relief=tk.FLAT, bd=0); qr_label.pack(padx=10, pady=10)
            # 计算并设置居中位置
            self.root.update_idletasks(); main_x = self.root.winfo_rootx(); main_y = self.root.winfo_rooty(); main_w = self.root.winfo_width(); main_h = self.root.winfo_height()
            self.qr_window.update_idletasks(); qr_w = self.qr_window.winfo_width(); qr_h = self.qr_window.winfo_height()
            x_pos = main_x + (main_w // 2) - (qr_w // 2); y_pos = main_y + (main_h // 2) - (qr_h // 2)
            self.qr_window.geometry(f"+{max(0, x_pos)}+{max(0, y_pos)}")
        except Exception as e: _log_message(self.log_queue, f"创建二维码窗口时出错: {e}"); traceback.print_exc()

    def _close_qr_window(self):
        """请求主线程关闭二维码窗口"""
        self.root.after(0, self._destroy_qr_window_in_main_thread)

    def _destroy_qr_window_in_main_thread(self):
         """在主GUI线程中销毁二维码窗口"""
         try:
             if self.qr_window and self.qr_window.winfo_exists(): self.qr_window.destroy()
         except Exception as e: print(f"Error destroying QR window: {e}")
         finally: self.qr_window = None; self._qr_tk_image_ref = None # 清理引用

    def _cancel_login(self):
         """当用户关闭二维码窗口时调用"""
         _log_message(self.log_queue, "用户关闭二维码窗口，取消登录。")
         self.login_stop_event.set() # 通知登录线程停止
         self._close_qr_window()    # 关闭窗口
         self.log_queue.put("LOGIN_PROCESS_FINISHED") # 通知主线程处理后续状态

    def _start_login(self):
        """开始登录流程"""
        if self.is_running: _log_message(self.log_queue, "任务运行时无法登录。"); return # 防止任务运行时登录
        # 更新GUI状态
        self.login_button.config(state=tk.DISABLED)
        self.login_status_label.config(text="状态: 请求二维码...", foreground="orange")
        self.login_stop_event.clear() # 重置登录停止事件
        self.is_logged_in = False     # 重置登录状态
        # 创建并启动登录后台线程
        login_thread = threading.Thread(target=self._perform_login_threaded, args=(self.log_queue, self._display_qr_code_window, self.login_stop_event), daemon=True); login_thread.start()

    def _perform_login_threaded(self, log_queue, qr_callback, stop_event):
        """登录后台线程的工作函数"""
        login_cookies = None
        try: login_cookies = login_via_qrcode(log_queue, qr_callback, stop_event) # 调用实际的登录函数
        except Exception as e: _log_message(log_queue, f"登录线程异常: {e}"); traceback.print_exc() # 记录异常

        if stop_event.is_set(): # 如果被中途取消
            _log_message(log_queue,"登录线程收到停止信号。")
            log_queue.put("LOGIN_PROCESS_FINISHED") # 确保发送结束信号
            return

        # 根据登录结果发送消息到队列
        if login_cookies:
            self.cookies = login_cookies; self.csrf_token = login_cookies.get('bili_jct')
            self.session = requests.Session(); self.session.cookies.update(self.cookies) # 创建带有cookies的session
            log_queue.put("LOGIN_SUCCESS")
        else:
             log_queue.put("LOGIN_FAILED")

    def _set_config_state(self, state):
        """启用或禁用配置相关的控件"""
        try:
            self.uid_entry.config(state=state)
            self.max_likes_entry.config(state=state)
            self.interval_entry.config(state=state)
            # 登录按钮的状态取决于配置状态和是否已登录
            if state == tk.DISABLED: self.login_button.config(state=tk.DISABLED)
            else: self.login_button.config(state=tk.NORMAL if not self.is_logged_in else tk.DISABLED)
        except tk.TclError as e: print(f"GUI Config State Error: {e}") # 处理控件可能已销毁的错误
        except Exception as e: print(f"Unexpected GUI Config State Error: {e}")

    def _start_stop_liking(self):
        """处理“启动/中止任务”按钮的点击事件"""
        if not self.is_logged_in: messagebox.showerror("错误", "请先登录！", parent=self.root); return # 未登录则提示

        if self.is_running: # 如果当前正在运行，则执行停止逻辑
            _log_message(self.log_queue, "收到中止任务请求...")
            self.stop_event.set() # 设置停止信号
            self.action_button.config(state=tk.DISABLED) # 暂时禁用按钮，防止重复点击
            self.status_bar.config(text="正在中止任务...") # 更新状态栏
        else: # 如果当前未运行，则执行启动逻辑
            # 获取并验证用户输入
            uid = self.uid_entry.get().strip(); max_likes_str = self.max_likes_entry.get().strip(); interval_sec_str = self.interval_entry.get().strip()
            if not uid.isdigit(): messagebox.showerror("错误", "目标UID必须是纯数字！", parent=self.root); return
            try: max_likes = int(max_likes_str); assert max_likes > 0
            except (ValueError, AssertionError): messagebox.showerror("错误", "初始点赞数必须是正整数！", parent=self.root); return
            try: interval_sec = float(interval_sec_str); assert interval_sec > 0
            except (ValueError, AssertionError): messagebox.showerror("错误", "监控间隔秒数必须是正数！", parent=self.root); return

            # 记录启动日志
            _log_message(self.log_queue, f"启动任务: UID={uid}, 初始上限={max_likes}, 间隔={interval_sec:.1f}秒")
            self.stop_event.clear() # 清除之前的停止信号
            # 检查session是否存在
            if not self.session: _log_message(self.log_queue, "错误：内部会话未初始化。"); messagebox.showerror("错误", "登录会话丢失，请重新登录。", parent=self.root); self.is_logged_in = False; self.login_status_label.config(text="状态: 未登录", foreground=FG_TEXT_MUTED); self.action_button.config(state=tk.DISABLED); self.login_button.config(state=tk.NORMAL); return
            # 创建并启动后台任务线程
            self.backend_thread = threading.Thread(target=self._run_backend_process, args=(int(uid), max_likes, interval_sec, self.session, self.csrf_token, self.log_queue, self.stop_event), daemon=True);
            self.log_queue.put("BACKEND_STARTED"); # 通知GUI任务已启动
            self.backend_thread.start()

    def _run_backend_process(self, target_uid, max_initial_likes, polling_interval_seconds, session, csrf_token, log_queue, stop_event):
        """后台核心工作线程，执行初始扫描和监控点赞任务"""
        latest_dynamic_id_overall = "0"; error_occurred = False; stop_message_sent = False
        try:
            # --- Phase 1: Initial Scan ---
            _log_message(log_queue, f"--- Phase 1: 初始扫描 UID={target_uid} ---")
            liked_count_initial = 0; processed_dynamic_ids_initial = set(); current_offset = "0"; has_more_dynamics = True; page_counter = 0; max_pages_to_scan = 200; like_delay_min=1.5; like_delay_max=3.0; page_gap_delay_min=2.0; page_gap_delay_max=4.0
            while liked_count_initial < max_initial_likes and has_more_dynamics and page_counter < max_pages_to_scan:
                if stop_event.is_set(): _log_message(log_queue, "初始扫描被中断。"); break
                page_counter += 1; _log_message(log_queue, f"初始扫描: 获取第 {page_counter} 批 (Offset: {current_offset})")
                dynamics_batch, next_offset, has_more = get_up_dynamics(session, target_uid, current_offset, log_queue, stop_event)
                if stop_event.is_set(): break
                if dynamics_batch is None: _log_message(log_queue, "初始扫描获取失败，终止扫描阶段。"); break
                has_more_dynamics = has_more; current_batch_latest_id = "0"
                if not dynamics_batch: # 处理空批次
                    if current_offset=="0": _log_message(log_queue,"初始扫描: 未找到动态。")
                    else: _log_message(log_queue,"初始扫描: 无更多动态。")
                    if next_offset == current_offset and current_offset != "0": has_more_dynamics = False
                else: # 处理非空批次
                    _log_message(log_queue, f"初始扫描: 获取到 {len(dynamics_batch)} 条，处理中...")
                    # 先更新最新ID
                    for dynamic_data in dynamics_batch:
                         dynamic_id = dynamic_data.get("dynamic_id", "0");
                         if dynamic_id > current_batch_latest_id: current_batch_latest_id = dynamic_id
                    if current_batch_latest_id > latest_dynamic_id_overall: latest_dynamic_id_overall = current_batch_latest_id
                    # 再处理点赞
                    for dynamic_data in dynamics_batch:
                        if liked_count_initial >= max_initial_likes: break
                        if stop_event.is_set(): break
                        dynamic_id = dynamic_data.get("dynamic_id"); needs_like = dynamic_data.get("needs_like", False); desc_text = dynamic_data.get("desc_text", f"ID {dynamic_id}")
                        if not dynamic_id or dynamic_id in processed_dynamic_ids_initial: continue
                        processed_dynamic_ids_initial.add(dynamic_id); _log_message(log_queue, f"检查动态: {desc_text}")
                        if needs_like:
                            _log_message(log_queue, "检测到未点赞，尝试点赞...")
                            like_success = like_dynamic(session, dynamic_id, csrf_token, log_queue, stop_event)
                            if stop_event.is_set(): break
                            if like_success: liked_count_initial += 1; _log_message(log_queue, f"初始点赞计数: {liked_count_initial}/{max_initial_likes}")
                            stop_event.wait(timeout=random.uniform(like_delay_min, like_delay_max)) # 点赞后等待
                        else: stop_event.wait(timeout=random.uniform(0.2, 0.5)) # 未点赞短暂等待
                # 检查是否需要退出外层循环
                if liked_count_initial >= max_initial_likes or stop_event.is_set(): break
                # 准备下一批
                current_offset = next_offset
                if has_more_dynamics: _log_message(log_queue, f"完成第 {page_counter} 批处理，准备下一批..."); stop_event.wait(timeout=random.uniform(page_gap_delay_min, page_gap_delay_max)) # 批次间等待

            # 初始扫描结束处理
            if not stop_event.is_set(): # 只有未被中断时才记录完成信息
                 _log_message(log_queue, "--- 初始扫描阶段完成 ---"); _log_message(log_queue, f"初始阶段点赞 {liked_count_initial} 条。最新动态ID: {latest_dynamic_id_overall}")
            else: return # 如果是被中断，直接返回，不进入监控

            # --- Phase 2: Monitoring ---
            _log_message(log_queue, f"--- Phase 2: 进入监控模式 (间隔: {polling_interval_seconds:.1f} 秒) ---"); processed_dynamic_ids_monitor = set(processed_dynamic_ids_initial)
            while not stop_event.is_set(): # 监控主循环
                # 计算并执行等待
                wait_time = polling_interval_seconds * random.uniform(0.9, 1.1); _log_message(log_queue, f"监控: 等待 {wait_time:.1f} 秒..."); stop_event.wait(timeout=wait_time);
                if stop_event.is_set(): break # 等待后再次检查

                # 获取最新动态
                _log_message(log_queue, f"监控: 检查新动态 (上次最新ID: {latest_dynamic_id_overall})..."); dynamics_latest_batch, _, _ = get_up_dynamics(session, target_uid, "0", log_queue, stop_event)
                if stop_event.is_set(): break
                if dynamics_latest_batch is None: _log_message(log_queue, "监控: 获取最新动态失败，稍后重试。"); continue # 获取失败则跳过本轮

                # 处理获取到的最新动态
                new_dynamics_to_like = []; current_check_latest_id = "0"
                for dynamic_data in dynamics_latest_batch:
                    dynamic_id = dynamic_data.get("dynamic_id", "0");
                    if not dynamic_id or dynamic_id == "0": continue
                    # 更新本轮检查到的最新ID
                    if dynamic_id > current_check_latest_id: current_check_latest_id = dynamic_id
                    # 判断是否是需要点赞的新动态
                    if dynamic_id > latest_dynamic_id_overall and dynamic_id not in processed_dynamic_ids_monitor:
                        needs_like = dynamic_data.get("needs_like", False);
                        if needs_like: new_dynamics_to_like.append(dynamic_id)
                        processed_dynamic_ids_monitor.add(dynamic_id) # 标记为已处理（无论是否点赞）
                # 更新全局最新ID标记
                if current_check_latest_id > latest_dynamic_id_overall: _log_message(log_queue, f"监控: 更新最新动态 ID 为 {current_check_latest_id}"); latest_dynamic_id_overall = current_check_latest_id

                # 点赞新发现的动态
                if new_dynamics_to_like:
                    _log_message(log_queue, f"监控: 发现 {len(new_dynamics_to_like)} 条新动态需点赞..."); liked_in_monitor_batch = 0
                    for dyn_id in reversed(new_dynamics_to_like): # 从旧到新点赞
                        if stop_event.is_set(): break
                        _log_message(log_queue, f"  尝试点赞新动态 ID: {dyn_id}"); like_success = like_dynamic(session, dyn_id, csrf_token, log_queue, stop_event)
                        if stop_event.is_set(): break
                        if like_success: liked_in_monitor_batch += 1
                        stop_event.wait(timeout=random.uniform(like_delay_min, like_delay_max)) # 点赞后等待
                    if not stop_event.is_set(): _log_message(log_queue, f"监控: 本轮点赞完成，成功 {liked_in_monitor_batch} 条。")
                else: _log_message(log_queue, "监控: 未发现需点赞的新动态。")

        # 处理后台线程中的严重错误
        except RuntimeError as e: _log_message(log_queue, f"严重运行时错误: {e}。线程终止。"); error_occurred = True; traceback.print_exc()
        # 处理其他意外错误
        except Exception as e: _log_message(log_queue, f"后台线程发生意外错误: {e}"); log_queue.put(traceback.format_exc()); error_occurred = True
        # 线程结束（正常退出监控循环虽然理论上不会发生，但以防万一）或异常终止时，确保发送停止消息
        finally:
            if not stop_message_sent:
                if stop_event.is_set(): log_queue.put("BACKEND_STOPPED_MANUAL") # 如果是收到停止信号
                elif error_occurred: log_queue.put("BACKEND_STOPPED_ERROR")      # 如果是发生错误
                else: log_queue.put("BACKEND_STOPPED_MANUAL")                 # 其他情况按手动停止处理
                stop_message_sent = True

    def _on_closing(self):
        """处理主窗口关闭事件"""
        should_exit = True
        if self.is_running: # 如果后台任务在运行，弹出确认框
            should_exit = messagebox.askyesno("确认退出", "点赞/监控任务仍在运行中，确定要停止并退出吗？", parent=self.root)

        if should_exit: # 如果确认退出或任务未运行
            _log_message(self.log_queue, "收到退出请求，正在停止后台任务...")
            # 设置所有停止信号
            self.stop_event.set()
            self.login_stop_event.set()
            self._close_qr_window() # 关闭可能存在的二维码窗口
            # 等待后台线程结束（设置超时）
            if self.backend_thread and self.backend_thread.is_alive():
                 if threading.current_thread() != self.backend_thread: # 避免线程自锁
                     self.backend_thread.join(timeout=1.5)
                 else: print("警告: _on_closing 可能在后台线程中被调用?") # 不应发生
            # 销毁主窗口
            try: self.root.destroy()
            except tk.TclError as e: print(f"Error destroying main window: {e}") # 处理窗口可能已销毁的错误

# --- 程序主入口 ---
if __name__ == "__main__":
    # 打印依赖提示
    print("="*40); print("提示: 确保已安装 pip install requests qrcode Pillow brotli"); print("="*40 + "\n")
    root = None # 初始化root变量
    try:
        root = tk.Tk() # 创建Tk根窗口
        app = BiliLikerApp(root) # 实例化GUI应用
        root.mainloop() # 进入Tkinter事件循环
    except Exception as e: # 捕获顶层异常
        print(f"\nGUI启动或运行期间发生未捕获的严重错误: {e}"); traceback.print_exc()
    finally: # 确保最后打印退出信息
        print("程序执行完毕。")