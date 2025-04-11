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
import http.cookiejar
# --- 新增: Wbi 签名所需 ---
from functools import reduce
from hashlib import md5
from urllib.parse import urlencode

# --- 自定义模块导入 ---
from login import login_via_qrcode # 假设 login.py 在同一目录

# --- 界面颜色主题定义 (浅色清爽主题) ---
BG_LIGHT_PRIMARY = "#F5F5F5"; BG_WIDGET_ALT = "#FFFFFF"; FG_TEXT_DARK = "#212121"
FG_TEXT_MUTED = "#616161"; ACCENT_BRIGHT_BLUE = "#2979FF"; BORDER_LIGHT = "#E0E0E0"
BUTTON_FG = "#FFFFFF"; BUTTON_ACTIVE_BLUE = "#0D47A1"; STATUS_BG = "#EEEEEE"
STATUS_FG = FG_TEXT_DARK; ERROR_FG = "#D32F2F"; SUCCESS_FG = "#388E3C"


# --- Bilibili API 相关定义 ---
DYNAMICS_FETCH_URL = "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space"
LIKE_DYNAMIC_URL = "https://api.vc.bilibili.com/dynamic_like/v1/dynamic_like/thumb"
GET_DYNAMIC_DETAIL_URL = "https://api.vc.bilibili.com/dynamic_svr/v1/dynamic_svr/get_dynamic_detail"
NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
HEADERS = {
    'Accept': 'application/json, text/plain, */*', 'Accept-Encoding': 'gzip, deflate, br',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8', 'Origin': 'https://t.bilibili.com',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36'
}
# --- Brotli 库检查 ---
try: import brotli
except ImportError: print("警告：未找到 'brotli' 库，建议运行: pip install brotli")

# --- 全局日志记录辅助函数 ---
def _log_message(log_queue, message, target_uid=None):
    if log_queue:
        try: log_queue.put({'target': target_uid if target_uid else 'main', 'message': f"[{time.strftime('%H:%M:%S')}] {str(message)}"})
        except Exception as e: print(f"[{time.strftime('%H:%M:%S')}] {str(message)}"); print(f"Queue Error: {e}")
    else: prefix = f"[UID:{target_uid}] " if target_uid else "[Main] "; print(f"[{time.strftime('%H:%M:%S')}] {prefix}{str(message)}")

# --- 资源路径辅助函数 ---
def resource_path(relative_path):
    try: base_path = sys._MEIPASS
    except Exception: base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# --- Wbi 签名实现 ---
mixinKeyEncTab = [ 46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52 ]
def getMixinKey(orig: str): return reduce(lambda s, i: s + orig[i], mixinKeyEncTab, '')[:32]
def encWbi(params: dict, img_key: str, sub_key: str):
    mixin_key = getMixinKey(img_key + sub_key); curr_time = round(time.time())
    params['wts'] = curr_time; params = dict(sorted(params.items()))
    params = { k: ''.join(filter(lambda chr: chr not in "!'()*", str(v))) for k, v in params.items() }
    query = urlencode(params); wbi_sign = md5((query + mixin_key).encode()).hexdigest()
    params['w_rid'] = wbi_sign; return params
wbi_keys = {"img_key": None, "sub_key": None, "timestamp": 0}; wbi_keys_lock = threading.Lock()
def get_wbi_keys_cached(session, log_queue):
    with wbi_keys_lock:
        current_time = time.time()
        if wbi_keys["img_key"] and wbi_keys["sub_key"] and (current_time - wbi_keys["timestamp"] < 3600): return wbi_keys["img_key"], wbi_keys["sub_key"]
        _log_message(log_queue, "正在获取最新的 Wbi Keys...")
        try:
            response = session.get(NAV_URL, headers=HEADERS, timeout=10); response.raise_for_status(); json_content = response.json()
            wbi_img = json_content.get('data', {}).get('wbi_img', {}); img_url = wbi_img.get('img_url'); sub_url = wbi_img.get('sub_url')
            if not img_url or not sub_url: _log_message(log_queue, "错误: 未能在 nav API 响应中找到 img_url 或 sub_url"); return None, None
            img_key = img_url.split('/')[-1].split('.')[0]; sub_key = sub_url.split('/')[-1].split('.')[0]
            wbi_keys["img_key"] = img_key; wbi_keys["sub_key"] = sub_key; wbi_keys["timestamp"] = current_time
            _log_message(log_queue, "成功获取并缓存 Wbi Keys"); return img_key, sub_key
        except requests.exceptions.RequestException as e: _log_message(log_queue, f"获取 Wbi Keys 时网络错误: {e}"); return None, None
        except Exception as e: _log_message(log_queue, f"获取 Wbi Keys 时发生错误: {e}"); traceback.print_exc(); return None, None
# --- Wbi 签名结束 ---


# --- 后台网络请求与逻辑函数 ---
def get_up_dynamics(session, host_mid, offset, log_queue, stop_event):
    """获取指定UP主的动态列表 (使用 Polymer API + Wbi 签名)。"""
    img_key, sub_key = get_wbi_keys_cached(session, log_queue)
    if not img_key or not sub_key: _log_message(log_queue, f"错误: 无法获取 Wbi Keys (UID:{host_mid})", target_uid=host_mid); return None, None, None, None
    params = {"host_mid": host_mid, "offset": offset, "timezone_offset": -480 }
    signed_params = encWbi(params.copy(), img_key, sub_key)
    dynamic_headers = HEADERS.copy(); dynamic_headers['Referer'] = f'https://t.bilibili.com/?tab=all'
    max_retries=3; initial_retry_delay=6; retries = 0; retry_delay = initial_retry_delay
    while retries <= max_retries:
        if stop_event.is_set(): return None, None, None, None
        response = None
        try:
            response = session.get(DYNAMICS_FETCH_URL, params=signed_params, headers=dynamic_headers, timeout=25)
            response.raise_for_status()
            if stop_event.is_set(): return None, None, None, None
            try: data = response.json()
            except json.JSONDecodeError:
                _log_message(log_queue, f"错误: JSON解析失败 (UID:{host_mid}, Offset:'{offset}')", target_uid=host_mid)
                if response.headers.get('Content-Encoding') == 'br': _log_message(log_queue, "提示：检查 'brotli' 库。", target_uid=host_mid)
                if retries < 1: _log_message(log_queue, f"将在 {retry_delay:.1f} 秒后重试(JSON)...", target_uid=host_mid); time.sleep(retry_delay); retries += 1; retry_delay *= 1.5; continue
                else: _log_message(log_queue, f"JSON错误达到最大重试次数。", target_uid=host_mid); return None, None, None, None
            api_code = data.get("code"); api_message = data.get("message", "")
            if api_code == 0:
                dynamics_data = data.get("data", {}); items = dynamics_data.get("items", [])
                has_more = dynamics_data.get("has_more", False); next_offset = dynamics_data.get("offset", "")
                extracted_list = []; host_uname = f"UID_{host_mid}"
                for item in items:
                    if stop_event.is_set(): return None, None, None, None
                    dynamic_id = item.get("id_str")
                    if not dynamic_id or dynamic_id == "0": continue
                    try:
                        author_info = item.get('modules', {}).get('module_author', {})
                        if author_info.get('name'): host_uname = author_info['name']
                        elif item.get('basic', {}).get('name'): host_uname = item['basic']['name']
                    except Exception: pass
                    effective_like_status = 0
                    try:
                        like_info = item.get('modules', {}).get('module_stat', {}).get('like_info', {})
                        if like_info.get('is_liked') == 1: effective_like_status = 1
                    except AttributeError: pass
                    desc_text = f"动态 ID: {dynamic_id}"
                    try:
                         dyn_module = item.get('modules', {}).get('module_dynamic', {})
                         if dyn_module.get('desc') and dyn_module['desc'].get('text'): desc_text = dyn_module['desc']['text']
                         elif dyn_module.get('major',{}).get('draw',{}).get('items'): desc_text = f"[图片] {len(dyn_module['major']['draw']['items'])} 图"
                         elif dyn_module.get('major',{}).get('archive',{}).get('title'): desc_text = f"[视频] {dyn_module['major']['archive']['title']}"
                         elif dyn_module.get('major',{}).get('article',{}).get('title'): desc_text = f"[专栏] {dyn_module['major']['article']['title']}"
                    except Exception: pass
                    item_data = { "dynamic_id": dynamic_id, "needs_like": (effective_like_status == 0), "desc_text": desc_text[:60] + ('...' if len(desc_text) > 60 else ''), "uname": host_uname}
                    extracted_list.append(item_data)
                last_processed_dynamic_id = extracted_list[-1]['dynamic_id'] if extracted_list else None # Get last ID if list not empty
                next_request_offset = next_offset # Use API's offset
                if next_offset == offset and offset != "": has_more = False # Check if offset is stuck
                return extracted_list, next_request_offset, has_more, host_uname
            if api_code == -352:
                _log_message(log_queue, f"错误: 请求校验失败 (Wbi sign error?) (UID:{host_mid}, code={api_code})", target_uid=host_mid)
                if retries == 0:
                    _log_message(log_queue, "尝试刷新 Wbi Keys 并重试...", target_uid=host_mid)
                    with wbi_keys_lock: # Correct indentation
                         wbi_keys["timestamp"] = 0
                    retries += 1; time.sleep(1); continue
                else: _log_message(log_queue, "刷新 Wbi Keys 后重试仍然失败。", target_uid=host_mid); return None, None, None, None
            is_rate_limited = ("频繁" in api_message or api_code in [-799, -412, -509, 4128002, -404])
            if is_rate_limited and retries < max_retries: _log_message(log_queue, f"API限制/错误 (code={api_code})，稍后重试...", target_uid=host_mid); time.sleep(retry_delay); retries += 1; retry_delay *= 1.8; continue
            else:
                _log_message(log_queue, f"获取动态失败: code={api_code}, msg='{api_message}'", target_uid=host_mid);
                if is_rate_limited: _log_message(log_queue, f"已达最大重试次数({max_retries})。", target_uid=host_mid)
                if api_code == -101: _log_message(log_queue, "错误: 登录状态失效。", target_uid=host_mid); raise RuntimeError("登录失效(fetch)")
                return None, None, None, None
        except requests.exceptions.HTTPError as e:
             if response is not None and response.status_code == 412:
                 _log_message(log_queue, f"失败: HTTP 412 (风控/签名/频率?)", target_uid=host_mid)
                 if retries < max_retries:
                     http_412_delay = retry_delay * 1.5 + random.uniform(1,3); _log_message(log_queue, f"将在 {http_412_delay:.1f} 秒后重试(412)...", target_uid=host_mid)
                     if retries % 2 == 0: _log_message(log_queue, "尝试强制刷新 Wbi Keys...", target_uid=host_mid);
                     with wbi_keys_lock: wbi_keys["timestamp"] = 0
                     time.sleep(http_412_delay); retries += 1; retry_delay *= 2; continue
                 else: _log_message(log_queue, f"遇412错误达到最大重试次数。", target_uid=host_mid); return None, None, None, None
             else:
                 _log_message(log_queue, f"HTTP错误: {e}", target_uid=host_mid)
                 if retries < max_retries: _log_message(log_queue, f"将在 {retry_delay:.1f} 秒后重试(HTTP)...", target_uid=host_mid); time.sleep(retry_delay); retries += 1; retry_delay *= 1.5; continue
                 else: _log_message(log_queue, "HTTP错误达到最大重试次数。", target_uid=host_mid); return None, None, None, None
        except requests.exceptions.Timeout: _log_message(log_queue, f"超时", target_uid=host_mid);
        except requests.exceptions.RequestException as e: _log_message(log_queue, f"网络错误: {e}", target_uid=host_mid);
        except RuntimeError as e: raise e
        except Exception as e: _log_message(log_queue, f"未知错误: {e}", target_uid=host_mid); traceback.print_exc(); return None, None, None, None
        if retries < max_retries: _log_message(log_queue, f"出错，{retry_delay:.1f}秒后重试...", target_uid=host_mid); time.sleep(retry_delay); retries += 1; retry_delay *= 1.5; continue
        else: _log_message(log_queue, f"达到最大重试次数。", target_uid=host_mid); return None, None, None, None
    return None, None, None, None


def get_single_dynamic_detail(session, dynamic_id, log_queue, target_uid=None):
    # ... (代码保持不变) ...
    params = {"dynamic_id": dynamic_id}
    detail_headers = HEADERS.copy(); detail_headers['Referer'] = f'https://t.bilibili.com/{dynamic_id}'
    try:
        response = session.get(GET_DYNAMIC_DETAIL_URL, params=params, headers=detail_headers, timeout=10)
        response.raise_for_status(); data = response.json()
        if data.get("code") == 0: return data.get("data", {}).get("card")
        else: _log_message(log_queue, f"获取动态详情失败: ID={dynamic_id}, Code={data.get('code')}, Msg={data.get('message')}", target_uid=target_uid); return None
    except (requests.exceptions.RequestException, json.JSONDecodeError, Exception) as e: _log_message(log_queue, f"获取动态详情异常: ID={dynamic_id}, Error={e}", target_uid=target_uid); return None

def like_dynamic(session, dynamic_id, csrf_token, log_queue, stop_event, target_uid=None):
    # ... (代码保持不变) ...
    payload = { "dynamic_id": dynamic_id, "up": 1, "csrf": csrf_token }
    dynamic_headers = HEADERS.copy(); dynamic_headers['Referer'] = f'https://t.bilibili.com/{dynamic_id}'; dynamic_headers['Origin'] = 'https://t.bilibili.com'
    max_like_attempts=3; current_attempt=0; base_like_delay=1.5
    while current_attempt < max_like_attempts:
        if stop_event.is_set(): return False
        current_attempt += 1
        if current_attempt > 1:
            if stop_event.is_set(): return False
            retry_like_delay = (2**(current_attempt-2)) * base_like_delay + random.uniform(0.1, 0.3); time.sleep(retry_like_delay)
        if stop_event.is_set(): return False
        response = None
        try:
            response = session.post(LIKE_DYNAMIC_URL, data=payload, headers=dynamic_headers, timeout=15)
            if stop_event.is_set(): return False
            response.raise_for_status()
            try:
                data = response.json()
                api_code = data.get("code"); api_message = data.get("message","")
                like_request_success = False
                if api_code == 0: _log_message(log_queue, f"点赞请求成功: ID={dynamic_id}", target_uid=target_uid); like_request_success = True
                elif api_code == 71000: _log_message(log_queue, f"已点赞过: ID={dynamic_id}", target_uid=target_uid); return True
                if like_request_success:
                    verify_delay = random.uniform(1.0, 2.0); time.sleep(verify_delay)
                    if stop_event.is_set(): return False
                    detail_card = get_single_dynamic_detail(session, dynamic_id, log_queue, target_uid)
                    if stop_event.is_set(): return False
                    if detail_card:
                        desc = detail_card.get('desc')
                        if desc:
                             like_state = desc.get('like_state'); is_liked_field = desc.get('is_liked'); final_like_status = 0
                             if isinstance(like_state, int): final_like_status = like_state
                             elif isinstance(is_liked_field, int): final_like_status = is_liked_field
                             if final_like_status == 1: _log_message(log_queue, f"  确认点赞成功: ID={dynamic_id}", target_uid=target_uid); return True
                             else: _log_message(log_queue, f"  警告: 点赞请求成功但状态确认失败: ID={dynamic_id}", target_uid=target_uid); return False
                        else: _log_message(log_queue, f"  警告: 无法从详情确认点赞状态(no desc): ID={dynamic_id}", target_uid=target_uid); return False
                    else: _log_message(log_queue, f"  警告: 无法获取详情确认点赞状态: ID={dynamic_id}", target_uid=target_uid); return False
                elif api_code in [-412, -509, 4128002] or "频繁" in api_message: _log_message(log_queue, f"点赞速率限制: ID={dynamic_id}, code={api_code}", target_uid=target_uid); continue
                elif api_code == -111: _log_message(log_queue, f"错误: CSRF校验失败: ID={dynamic_id}", target_uid=target_uid); raise RuntimeError("CSRF失效(like)")
                elif api_code == -101: _log_message(log_queue, f"错误: 账号未登录: ID={dynamic_id}", target_uid=target_uid); raise RuntimeError("登录失效(like)")
                elif api_code == -400: _log_message(log_queue, f"错误: 无效请求 (ID={dynamic_id})。", target_uid=target_uid); return False
                else: _log_message(log_queue, f"点赞未知API错误: ID={dynamic_id}, code={api_code}", target_uid=target_uid); continue
            except json.JSONDecodeError:
                _log_message(log_queue, f"错误: 点赞响应JSON解析失败: ID={dynamic_id}", target_uid=target_uid)
                if response.headers.get('Content-Encoding') == 'br': _log_message(log_queue, "提示: 检查 'brotli' 库。", target_uid=target_uid)
                continue
        except requests.exceptions.HTTPError as e:
             status_code = response.status_code if response else "N/A"; _log_message(log_queue, f"点赞HTTP失败: ID={dynamic_id}, Status={status_code}, Error: {e}", target_uid=target_uid)
             if status_code in [401, 403]: raise RuntimeError(f"HTTP {status_code}(like)")
             elif status_code == 412: _log_message(log_queue, f"点赞HTTP 412错误(可能风控): ID={dynamic_id}", target_uid=target_uid)
             continue
        except requests.exceptions.Timeout: _log_message(log_queue, f"点赞超时: ID={dynamic_id}", target_uid=target_uid); continue
        except requests.exceptions.RequestException as e: _log_message(log_queue, f"点赞网络失败: ID={dynamic_id}, Err:{e}", target_uid=target_uid); continue
        except RuntimeError as e: raise e
        except Exception as e: _log_message(log_queue, f"点赞意外错误: ID={dynamic_id}, Err:{e}", target_uid=target_uid); traceback.print_exc(); return False
    _log_message(log_queue, f"点赞 ID {dynamic_id} 重试多次后失败。", target_uid=target_uid)
    return False

# --- GUI Application Class ---
class BiliLikerApp:
    def __init__(self, root):
        """初始化应用程序"""
        # ... (GUI 初始化代码保持不变) ...
        self.root = root; self.root.title("动态守护姬DynamicGuardian v1.3.1"); self.root.geometry("750x650"); self.root.minsize(700, 600); self.root.config(bg=BG_LIGHT_PRIMARY)
        self._app_icon = None
        try:
            icon_path = resource_path('app_icon.png');
            if os.path.exists(icon_path): icon_image = PhotoImage(file=icon_path); self.root.iconphoto(False, icon_image); self._app_icon = icon_image; print(f"成功加载并设置图标: {icon_path}")
            else: print(f"警告：图标文件未找到: '{icon_path}'")
        except Exception as e: print(f"设置图标失败: {e}"); traceback.print_exc()
        self.cookies_dict = None; self.csrf_token = None; self.session = requests.Session(); self.is_logged_in = False; self.is_running = False; self.backend_thread = None; self.stop_event = threading.Event(); self.log_queue = queue.Queue(); self.qr_window = None; self.login_stop_event = threading.Event(); self._qr_tk_image_ref = None
        self.cookie_file_path = "bilibili_cookies.txt"
        self.uid_log_widgets = {}
        self.default_font = tkFont.Font(family="Microsoft YaHei UI", size=10); self.label_font = tkFont.Font(family="Microsoft YaHei UI", size=10); self.button_font = tkFont.Font(family="Microsoft YaHei UI", size=10, weight='bold'); self.entry_font = tkFont.Font(family="Microsoft YaHei UI", size=10); self.label_frame_font = tkFont.Font(family="Microsoft YaHei UI", size=10, weight="bold"); self.log_font = tkFont.Font(family="Microsoft YaHei UI", size=9); self.text_widget_font = tkFont.Font(family="Consolas", size=10)
        self.style = ttk.Style();
        try: self.style.theme_use('clam')
        except tk.TclError: print("Clam theme not available.")
        self._apply_styles(); self._create_widgets(); self._create_menu()
        self._initialize_session_and_login()
        self.root.after(100, self._check_log_queue); self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def _apply_styles(self):
        """配置ttk部件的样式"""
        # ... (样式代码保持不变) ...
        self.style.configure('.', background=BG_LIGHT_PRIMARY, foreground=FG_TEXT_DARK, font=self.default_font, borderwidth=1)
        self.style.configure('TFrame', background=BG_LIGHT_PRIMARY)
        self.style.configure('TLabel', background=BG_LIGHT_PRIMARY, foreground=FG_TEXT_DARK, font=self.label_font)
        self.style.configure('TLabelframe', background=BG_LIGHT_PRIMARY, bordercolor=BORDER_LIGHT, borderwidth=1, relief=tk.GROOVE)
        self.style.configure('TLabelframe.Label', background=BG_LIGHT_PRIMARY, foreground=ACCENT_BRIGHT_BLUE, font=self.label_frame_font)
        self.style.configure('TButton', background=ACCENT_BRIGHT_BLUE, foreground=BUTTON_FG, bordercolor=ACCENT_BRIGHT_BLUE, borderwidth=0, padding=(10, 5), relief=tk.FLAT, font=self.button_font)
        self.style.map('TButton', background=[('pressed', BUTTON_ACTIVE_BLUE), ('active', ACCENT_BRIGHT_BLUE), ('disabled', BORDER_LIGHT)], foreground=[('disabled', FG_TEXT_MUTED)], relief=[('pressed', tk.FLAT)])
        self.style.configure('TEntry', fieldbackground=BG_WIDGET_ALT, foreground=FG_TEXT_DARK, insertcolor=FG_TEXT_DARK, bordercolor=BORDER_LIGHT, borderwidth=1, relief=tk.SOLID, font=self.entry_font)
        self.style.map('TEntry', bordercolor=[('focus', ACCENT_BRIGHT_BLUE)], fieldbackground=[('disabled', BG_LIGHT_PRIMARY)], foreground=[('disabled', FG_TEXT_MUTED)])
        self.style.configure('Status.TLabel', background=STATUS_BG, foreground=STATUS_FG, padding=(5, 3), font=self.default_font)
        self.style.configure('Stop.TButton', background=ERROR_FG, foreground=BUTTON_FG)
        self.style.map('Stop.TButton', background=[('pressed', '#B71C1C'), ('active', ERROR_FG), ('disabled', BORDER_LIGHT)])
        self.style.configure('TNotebook', background=BG_LIGHT_PRIMARY, borderwidth=0)
        self.style.configure('TNotebook.Tab', padding=(10, 5), font=self.default_font)
        self.style.map("TNotebook.Tab", background=[("selected", BG_LIGHT_PRIMARY)], foreground=[("selected", ACCENT_BRIGHT_BLUE)])

    def _create_menu(self):
        """创建顶部菜单栏"""
        # ... (菜单代码保持不变) ...
        menubar = tk.Menu(self.root); self.root.config(menu=menubar)
        help_menu = tk.Menu(menubar, tearoff=0, font=self.default_font); menubar.add_cascade(label="帮助(H)", menu=help_menu)
        help_menu.add_command(label="关于(A)...", command=self._show_about_window, font=self.default_font)

    def _show_about_window(self):
        """显示'关于'信息窗口"""
        # ... (关于窗口代码保持不变) ...
        about_window = tk.Toplevel(self.root); about_window.withdraw()
        about_window.title("关于 B站动态点赞助手"); about_window.resizable(False, False)
        about_window.transient(self.root); about_window.grab_set(); about_window.focus_set()
        about_window.config(bg=BG_LIGHT_PRIMARY)
        if self._app_icon:
            try: about_window.iconphoto(False, self._app_icon)
            except Exception as e: print(f"设置'关于'窗口图标失败: {e}")
        content_frame = ttk.Frame(about_window, padding="15", style='TFrame'); content_frame.pack(expand=True, fill=tk.BOTH)
        title_font = tkFont.Font(family="Microsoft YaHei UI", size=12, weight="bold")
        ttk.Label(content_frame, text="动态守护姬DynamicGuardian", font=title_font, style='TLabel').pack(pady=(0, 10))
        ttk.Label(content_frame, text="版本: 1.3.1", style='TLabel').pack(pady=2)
        ttk.Label(content_frame, text="作者: 四季Destination", style='TLabel').pack(pady=2)
        link_font = tkFont.Font(family="Microsoft YaHei UI", size=10, underline=True)
        github_link = tk.Label(content_frame, text="访问 GitHub 主页", fg=ACCENT_BRIGHT_BLUE, cursor="hand2", font=link_font, bg=BG_LIGHT_PRIMARY); github_link.pack(pady=5)
        github_link.bind("<Button-1>", lambda e: webbrowser.open_new_tab("https://github.com/forSeasons333"))
        bili_link = tk.Label(content_frame, text="访问 Bilibili 主页", fg=ACCENT_BRIGHT_BLUE, cursor="hand2", font=link_font, bg=BG_LIGHT_PRIMARY); bili_link.pack(pady=5)
        bili_link.bind("<Button-1>", lambda e: webbrowser.open_new_tab("https://space.bilibili.com/403039446"))
        ok_button = ttk.Button(content_frame, text="确定", command=about_window.destroy, width=10, style='TButton'); ok_button.pack(pady=(15, 0))
        about_window.update_idletasks(); win_w = about_window.winfo_width(); win_h = about_window.winfo_height()
        min_width = 420;
        if win_w < min_width: win_w = min_width
        main_x = self.root.winfo_rootx(); main_y = self.root.winfo_rooty(); main_w = self.root.winfo_width(); main_h = self.root.winfo_height()
        x_pos = main_x + (main_w // 2) - (win_w // 2); y_pos = main_y + (main_h // 2) - (win_h // 2)
        about_window.geometry(f"{win_w}x{win_h}+{max(0, x_pos)}+{max(0, y_pos)}")
        about_window.deiconify(); ok_button.focus_set()

    def _create_widgets(self):
        """创建主界面的所有控件"""
        # ... (控件创建代码保持不变) ...
        control_frame = ttk.Frame(self.root, padding="10", style='TFrame'); control_frame.pack(fill=tk.X, side=tk.TOP, anchor=tk.N)
        login_frame = ttk.LabelFrame(control_frame, text="登录", padding="5", style='TLabelframe'); login_frame.pack(fill=tk.X, pady=5)
        self.login_status_label = ttk.Label(login_frame, text="状态: 初始化...", width=20, foreground=FG_TEXT_MUTED, style='TLabel'); self.login_status_label.pack(side=tk.LEFT, padx=(5, 10), pady=5)
        self.login_button = ttk.Button(login_frame, text="扫码登录", command=self._start_login, width=12, style='TButton'); self.login_button.pack(side=tk.LEFT, padx=5, pady=5)
        self.logout_button = ttk.Button(login_frame, text="退出登录", command=self._logout, width=12, style='TButton', state=tk.DISABLED); self.logout_button.pack(side=tk.LEFT, padx=5, pady=5)
        config_frame = ttk.LabelFrame(control_frame, text="配置", padding="10", style='TLabelframe'); config_frame.pack(fill=tk.X, pady=5);
        uid_manage_frame = ttk.Frame(config_frame, style='TFrame'); uid_manage_frame.pack(fill=tk.X, pady=(5, 10)); uid_manage_frame.columnconfigure(1, weight=1)
        ttk.Label(uid_manage_frame, text="目标UID列表:", style='TLabel').grid(row=0, column=0, padx=5, sticky=tk.NW)
        uid_list_frame = ttk.Frame(uid_manage_frame, style='TFrame'); uid_list_frame.grid(row=0, column=1, rowspan=3, padx=5, sticky=tk.NSEW); uid_list_frame.rowconfigure(0, weight=1); uid_list_frame.columnconfigure(0, weight=1)
        self.uid_listbox = tk.Listbox(uid_list_frame, height=5, width=25, font=self.text_widget_font, bg=BG_WIDGET_ALT, fg=FG_TEXT_DARK, relief=tk.SOLID, borderwidth=1, selectbackground=ACCENT_BRIGHT_BLUE, selectforeground=BUTTON_FG, highlightthickness=1, highlightcolor=ACCENT_BRIGHT_BLUE, highlightbackground=BORDER_LIGHT)
        self.uid_list_scrollbar = ttk.Scrollbar(uid_list_frame, orient=tk.VERTICAL, command=self.uid_listbox.yview); self.uid_listbox['yscrollcommand'] = self.uid_list_scrollbar.set
        self.uid_listbox.grid(row=0, column=0, sticky=tk.NSEW); self.uid_list_scrollbar.grid(row=0, column=1, sticky=tk.NS)
        uid_entry_frame = ttk.Frame(uid_manage_frame, style='TFrame'); uid_entry_frame.grid(row=0, column=2, padx=(10, 5), sticky=tk.NW)
        ttk.Label(uid_entry_frame, text="添加UID:", style='TLabel').pack(anchor=tk.W)
        self.uid_add_entry_var = tk.StringVar(); self.uid_add_entry = ttk.Entry(uid_entry_frame, width=18, style='TEntry', textvariable=self.uid_add_entry_var); self.uid_add_entry.pack(anchor=tk.W, pady=(2, 5))
        self.add_uid_button = ttk.Button(uid_entry_frame, text="添加", command=self._add_uid, width=8, style='TButton'); self.add_uid_button.pack(anchor=tk.W)
        self.remove_uid_button = ttk.Button(uid_manage_frame, text="移除选中", command=self._remove_selected_uid, width=10, style='TButton'); self.remove_uid_button.grid(row=1, column=2, padx=(10, 5), pady=5, sticky=tk.NW)
        other_config_frame = ttk.Frame(config_frame, style='TFrame'); other_config_frame.pack(fill=tk.X, padx=5, pady=5); other_config_frame.columnconfigure(1, weight=1); other_config_frame.columnconfigure(3, weight=1)
        ttk.Label(other_config_frame, text="初始点赞数:", style='TLabel').grid(row=0, column=0, padx=(0,5), pady=3, sticky=tk.W)
        self.max_likes_entry = ttk.Entry(other_config_frame, width=8, style='TEntry'); self.max_likes_entry.grid(row=0, column=1, pady=3, sticky=tk.W); self.max_likes_entry.insert(0, "30")
        ttk.Label(other_config_frame, text="监控间隔(秒):", style='TLabel').grid(row=0, column=2, padx=(15,5), pady=3, sticky=tk.W)
        self.interval_entry = ttk.Entry(other_config_frame, width=8, style='TEntry'); self.interval_entry.grid(row=0, column=3, pady=3, sticky=tk.W); self.interval_entry.insert(0, "60")
        action_frame = ttk.Frame(control_frame, style='TFrame'); action_frame.pack(pady=(5, 10))
        self.action_button = ttk.Button(action_frame, text="启动任务", command=self._start_stop_liking, state=tk.DISABLED, width=15, style='TButton'); self.action_button.pack()
        log_notebook_frame = ttk.Frame(self.root, padding=(10, 0, 10, 5)); log_notebook_frame.pack(fill=tk.BOTH, expand=True)
        self.log_notebook = ttk.Notebook(log_notebook_frame, style='TNotebook'); self.log_notebook.pack(fill=tk.BOTH, expand=True)
        main_log_frame = ttk.Frame(self.log_notebook, padding=2, style='TFrame'); self.log_notebook.add(main_log_frame, text="主日志")
        self.main_log_text = scrolledtext.ScrolledText(main_log_frame, wrap=tk.WORD, state=tk.DISABLED, bd=1, relief=tk.SOLID, bg=BG_WIDGET_ALT, fg=FG_TEXT_DARK, insertbackground=FG_TEXT_DARK, font=self.log_font, borderwidth=1, highlightthickness=1, highlightcolor=ACCENT_BRIGHT_BLUE, highlightbackground=BORDER_LIGHT); self.main_log_text.pack(fill=tk.BOTH, expand=True)
        self.uid_log_widgets['main'] = self.main_log_text
        self.status_bar = ttk.Label(self.root, text="准备就绪", relief=tk.FLAT, anchor=tk.W, style='Status.TLabel', borderwidth=0); self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)


    def _add_uid(self):
        """添加用户输入的UID到列表框"""
        # ... (代码保持不变) ...
        uid_to_add = self.uid_add_entry_var.get().strip()
        if not uid_to_add.isdigit(): messagebox.showerror("错误", "请输入纯数字的有效UID！", parent=self.root); return
        current_uids = self.uid_listbox.get(0, tk.END)
        if uid_to_add in current_uids: messagebox.showwarning("提示", f"UID {uid_to_add} 已在列表中。", parent=self.root); self.uid_add_entry_var.set(""); return
        self.uid_listbox.insert(tk.END, uid_to_add); self.uid_add_entry_var.set("")
        _log_message(self.log_queue, f"已添加 UID: {uid_to_add}")

    def _remove_selected_uid(self):
        """从列表框移除当前选中的UID"""
        # ... (代码保持不变) ...
        selected_indices = self.uid_listbox.curselection()
        if not selected_indices: messagebox.showwarning("提示", "请先在列表中选择要移除的UID。", parent=self.root); return
        for index in reversed(selected_indices):
            removed_uid = self.uid_listbox.get(index); self.uid_listbox.delete(index)
            _log_message(self.log_queue, f"已移除 UID: {removed_uid}")

    # --- 添加回 _logout 方法 ---
    def _logout(self):
        """处理退出登录逻辑"""
        if not self.is_logged_in: _log_message(self.log_queue, "当前未登录，无需退出。"); return
        if self.is_running: messagebox.showwarning("提示", "请先中止当前任务再退出登录。", parent=self.root); return
        if messagebox.askyesno("确认退出登录", "确定要退出当前账号并删除本地 Cookie 文件吗？", parent=self.root):
            _log_message(self.log_queue, "正在退出登录...")
            self.is_logged_in = False; self.cookies_dict = None; self.csrf_token = None; self.session = requests.Session()
            try:
                if os.path.exists(self.cookie_file_path): os.remove(self.cookie_file_path); _log_message(self.log_queue, f"已删除本地 Cookie 文件: {self.cookie_file_path}")
            except Exception as e: _log_message(self.log_queue, f"警告: 删除 Cookie 文件失败: {e}")
            self.login_status_label.config(text="状态: 未登录", foreground=FG_TEXT_MUTED); self.action_button.config(state=tk.DISABLED); self.logout_button.config(state=tk.DISABLED); self.login_button.config(state=tk.NORMAL); self.status_bar.config(text="已退出登录")
            _log_message(self.log_queue, "退出登录完成。")
    # --- _logout 方法结束 ---

    # --- 其他方法 (_initialize_session_and_login, _check_cookie_valid, _save_cookies, 等等...) ---
    # ... (这些方法的代码与上一个包含 Cookie 支持的版本相同，此处省略) ...
    def _initialize_session_and_login(self):
        _log_message(self.log_queue, "正在初始化会话并检查本地 Cookie...")
        self.session = requests.Session()
        cookie_jar = http.cookiejar.MozillaCookieJar(self.cookie_file_path)
        if os.path.exists(self.cookie_file_path):
            try:
                cookie_jar.load(ignore_discard=True, ignore_expires=True); self.session.cookies.update(cookie_jar)
                _log_message(self.log_queue, f"成功加载本地 Cookie 文件: {self.cookie_file_path}")
                if self._check_cookie_valid():
                    _log_message(self.log_queue, "本地 Cookie 有效，自动登录成功。")
                    self.cookies_dict = self.session.cookies.get_dict(); self.csrf_token = self.session.cookies.get('bili_jct')
                    self.log_queue.put({'target':'main', 'message':"LOGIN_SUCCESS"})
                else: _log_message(self.log_queue, "本地 Cookie 已失效，请重新扫码登录。"); self.session.cookies.clear(); self.log_queue.put({'target':'main', 'message':"LOGIN_FAILED"})
            except Exception as e: _log_message(self.log_queue, f"加载 Cookie 文件失败: {e}，请扫码登录。"); self.session.cookies.clear(); self.log_queue.put({'target':'main', 'message':"LOGIN_FAILED"})
        else:
            _log_message(self.log_queue, "未找到本地 Cookie 文件，请扫码登录。")
            self.root.after(10, lambda: [self.login_status_label.config(text="状态: 未登录", foreground=FG_TEXT_MUTED), self.action_button.config(state=tk.DISABLED), self.logout_button.config(state=tk.DISABLED), self.login_button.config(state=tk.NORMAL)])

    def _check_cookie_valid(self):
        if not self.session or not self.session.cookies: return False
        _log_message(self.log_queue, "正在验证 Cookie 有效性...")
        try:
            response = self.session.get(NAV_URL, headers={'User-Agent': HEADERS['User-Agent'], 'Referer': 'https://www.bilibili.com/'}, timeout=10)
            response.raise_for_status(); data = response.json()
            is_login = data.get('data', {}).get('isLogin', False); uname = data.get('data', {}).get('uname', '未知用户')
            if data.get('code') == 0 and is_login: _log_message(self.log_queue, f"Cookie 验证成功，当前用户: {uname}"); return True
            else: _log_message(self.log_queue, f"Cookie 验证失败: Code={data.get('code')}, isLogin={is_login}"); return False
        except requests.exceptions.RequestException as e: _log_message(self.log_queue, f"Cookie 验证请求失败: {e}"); return False
        except Exception as e: _log_message(self.log_queue, f"Cookie 验证时发生未知错误: {e}"); traceback.print_exc(); return False

    def _save_cookies(self):
        if not self.session: _log_message(self.log_queue, "错误: 无法保存 Cookie，Session 未初始化。"); return
        cookie_jar = http.cookiejar.MozillaCookieJar(self.cookie_file_path)
        for cookie in self.session.cookies: cookie_jar.set_cookie(cookie)
        try: cookie_jar.save(ignore_discard=True, ignore_expires=True); _log_message(self.log_queue, f"Cookie 已成功保存到: {self.cookie_file_path}")
        except Exception as e: _log_message(self.log_queue, f"错误: 保存 Cookie 到文件失败: {e}"); traceback.print_exc()

    def _log_to_gui(self, target, message):
        log_widget = self.uid_log_widgets.get(target)
        if not log_widget: log_widget = self.uid_log_widgets.get('main'); message = f"[Target({target})? Addr? ] {message}"
        if log_widget:
            try: log_widget.config(state=tk.NORMAL); log_widget.insert(tk.END, str(message) + "\n"); log_widget.see(tk.END); log_widget.config(state=tk.DISABLED)
            except tk.TclError as e: print(f"GUI Log Error: {e}")
            except Exception as e: print(f"Unexpected GUI Log Error: {e}")

    def _check_log_queue(self):
        try:
            while not self.log_queue.empty():
                log_entry = self.log_queue.get_nowait()
                target = log_entry.get('target', 'main'); message = log_entry.get('message', '')
                if message == "LOGIN_SUCCESS": self.is_logged_in = True; self.login_status_label.config(text="状态: 已登录", foreground=SUCCESS_FG); self.action_button.config(state=tk.NORMAL); self.login_button.config(state=tk.DISABLED); self.logout_button.config(state=tk.NORMAL); self._close_qr_window(); self.status_bar.config(text="登录成功。")
                elif message == "LOGIN_FAILED": self.is_logged_in = False; self.login_status_label.config(text="状态: 登录失败", foreground=ERROR_FG); self.action_button.config(state=tk.DISABLED); self.login_button.config(state=tk.NORMAL); self.logout_button.config(state=tk.DISABLED); self._close_qr_window(); self.status_bar.config(text="登录失败，请重试。")
                elif message == "LOGIN_PROCESS_FINISHED":
                     if not self.is_logged_in: self.login_button.config(state=tk.NORMAL); self.login_status_label.config(text="状态: 未登录", foreground=FG_TEXT_MUTED)
                     self.logout_button.config(state=tk.DISABLED); self._close_qr_window()
                elif message == "BACKEND_STARTED": self.is_running = True; self.action_button.config(text="中止任务", style='Stop.TButton', state=tk.NORMAL); self.status_bar.config(text="运行中..."); self._set_config_state(tk.DISABLED); self.logout_button.config(state=tk.DISABLED)
                elif message == "BACKEND_STOPPED_MANUAL" or message == "BACKEND_STOPPED_ERROR": self.is_running = False; self.action_button.config(text="启动任务", style='TButton', state=tk.NORMAL if self.is_logged_in else tk.DISABLED); self.status_bar.config(text="已停止" if message.endswith("MANUAL") else "错误停止。"); self._set_config_state(tk.NORMAL); self.stop_event.clear(); self.logout_button.config(state=tk.NORMAL if self.is_logged_in else tk.DISABLED)
                else: self._log_to_gui(target, message)
        except queue.Empty: pass
        except Exception as e: self._log_to_gui('main', f"处理日志队列时出错: {e}"); traceback.print_exc()
        self.root.after(150, self._check_log_queue)

    def _display_qr_code_window(self, qr_pil_image):
        self.root.after(0, self._create_qr_window_in_main_thread, qr_pil_image)

    def _create_qr_window_in_main_thread(self, qr_pil_image):
        if self.qr_window and self.qr_window.winfo_exists(): self._close_qr_window()
        try:
            self.qr_window = tk.Toplevel(self.root); self.qr_window.title("扫描二维码登录"); self.qr_window.resizable(False, False)
            self.qr_window.transient(self.root); self.qr_window.attributes("-topmost", True)
            self.qr_window.protocol("WM_DELETE_WINDOW", self._cancel_login); self.qr_window.config(bg=BG_WIDGET_ALT)
            if self._app_icon:
                try: self.qr_window.iconphoto(False, self._app_icon)
                except Exception as e: print(f"设置QR窗口图标失败: {e}")
            self._qr_tk_image_ref = ImageTk.PhotoImage(qr_pil_image)
            qr_label = tk.Label(self.qr_window, image=self._qr_tk_image_ref, bg=BG_WIDGET_ALT, relief=tk.FLAT, bd=0)
            qr_label.pack(padx=10, pady=10)
            self.root.update_idletasks(); main_x = self.root.winfo_rootx(); main_y = self.root.winfo_rooty(); main_w = self.root.winfo_width(); main_h = self.root.winfo_height()
            self.qr_window.update_idletasks(); qr_w = self.qr_window.winfo_width(); qr_h = self.qr_window.winfo_height()
            x_pos = main_x + (main_w // 2) - (qr_w // 2); y_pos = main_y + (main_h // 2) - (qr_h // 2)
            self.qr_window.geometry(f"+{max(0, x_pos)}+{max(0, y_pos)}")
        except Exception as e: _log_message(self.log_queue, f"创建二维码窗口时出错: {e}"); traceback.print_exc()

    def _close_qr_window(self):
        self.root.after(0, self._destroy_qr_window_in_main_thread)

    def _destroy_qr_window_in_main_thread(self):
         try:
             if self.qr_window and self.qr_window.winfo_exists(): self.qr_window.destroy()
         except Exception as e: print(f"Error destroying QR window: {e}")
         finally: self.qr_window = None; self._qr_tk_image_ref = None

    def _cancel_login(self):
         _log_message(self.log_queue, "用户关闭二维码窗口，取消登录。"); self.login_stop_event.set(); self._close_qr_window(); self.log_queue.put({'target':'main', 'message':"LOGIN_PROCESS_FINISHED"})

    def _start_login(self):
        if self.is_running: _log_message(self.log_queue, "任务运行时无法登录。"); return
        if self.is_logged_in: _log_message(self.log_queue, "已登录，无需重复登录。"); return
        self.login_button.config(state=tk.DISABLED); self.logout_button.config(state=tk.DISABLED)
        self.login_status_label.config(text="状态: 请求二维码...", foreground="orange")
        self.login_stop_event.clear();
        login_thread = threading.Thread(target=self._perform_login_threaded, args=(self.log_queue, self._display_qr_code_window, self.login_stop_event), daemon=True); login_thread.start()

    def _perform_login_threaded(self, log_queue, qr_callback, stop_event):
        login_cookies_dict = None
        try: login_cookies_dict = login_via_qrcode(log_queue, qr_callback, stop_event)
        except Exception as e: _log_message(log_queue, f"登录线程异常: {e}"); traceback.print_exc()
        if stop_event.is_set(): _log_message(log_queue,"登录线程收到停止信号。"); log_queue.put({'target':'main', 'message':"LOGIN_PROCESS_FINISHED"}); return
        if login_cookies_dict:
            self.cookies_dict = login_cookies_dict; self.csrf_token = login_cookies_dict.get('bili_jct')
            self.session.cookies.clear(); requests.utils.add_dict_to_cookiejar(self.session.cookies, login_cookies_dict)
            self._save_cookies()
            log_queue.put({'target':'main', 'message':"LOGIN_SUCCESS"})
        else: log_queue.put({'target':'main', 'message':"LOGIN_FAILED"})

    def _set_config_state(self, state):
        """启用或禁用配置相关的控件"""
        try:
            listbox_state = tk.NORMAL if state == tk.NORMAL else tk.DISABLED; button_state = tk.NORMAL if state == tk.NORMAL else tk.DISABLED
            self.uid_listbox.config(state=listbox_state); self.uid_add_entry.config(state=state); self.add_uid_button.config(state=button_state); self.remove_uid_button.config(state=button_state)
            self.max_likes_entry.config(state=state); self.interval_entry.config(state=state)
            if state == tk.DISABLED: self.login_button.config(state=tk.DISABLED); self.logout_button.config(state=tk.DISABLED)
            else: self.login_button.config(state=tk.NORMAL if not self.is_logged_in else tk.DISABLED); self.logout_button.config(state=tk.NORMAL if self.is_logged_in else tk.DISABLED)
        except tk.TclError as e: print(f"GUI Config State Error: {e}")
        except Exception as e: print(f"Unexpected GUI Config State Error: {e}")

    def _start_stop_liking(self):
        """处理“启动/中止任务”按钮的点击事件 (多UID + Notebook)"""
        if not self.is_logged_in: messagebox.showerror("错误", "请先登录！", parent=self.root); return
        if self.is_running: _log_message(self.log_queue, "收到中止任务请求..."); self.stop_event.set(); self.action_button.config(state=tk.DISABLED); self.status_bar.config(text="正在中止任务...")
        else:
            target_uids_list = list(self.uid_listbox.get(0, tk.END))
            if not target_uids_list: messagebox.showerror("错误", "请先添加至少一个目标UID！", parent=self.root); return
            max_likes_str = self.max_likes_entry.get().strip(); interval_sec_str = self.interval_entry.get().strip()
            try: max_likes = int(max_likes_str); assert max_likes > 0
            except (ValueError, AssertionError): messagebox.showerror("错误", "初始点赞数必须是正整数！", parent=self.root); return
            try: interval_sec = float(interval_sec_str); assert interval_sec > 0
            except (ValueError, AssertionError): messagebox.showerror("错误", "监控间隔秒数必须是正数！", parent=self.root); return
            self._clear_and_create_log_tabs(target_uids_list)
            _log_message(self.log_queue, f"启动任务: UIDs={','.join(target_uids_list)}, 初始上限={max_likes}, 间隔={interval_sec:.1f}秒")
            self.stop_event.clear()
            if not self.session: _log_message(self.log_queue, "错误：内部会话未初始化。"); messagebox.showerror("错误", "登录会话丢失。", parent=self.root); self.is_logged_in = False; self.login_status_label.config(text="状态: 未登录", foreground=FG_TEXT_MUTED); self.action_button.config(state=tk.DISABLED); self.login_button.config(state=tk.NORMAL); return
            self.backend_thread = threading.Thread(target=self._run_backend_process, args=(target_uids_list, max_likes, interval_sec, self.session, self.csrf_token, self.log_queue, self.stop_event), daemon=True);
            self.log_queue.put({'target':'main', 'message':"BACKEND_STARTED"}); self.backend_thread.start()

    def _clear_and_create_log_tabs(self, uids_to_monitor):
        """清理旧的UID日志标签页并为新的UID列表创建标签页"""
        # ... (代码保持不变) ...
        current_tabs = list(self.log_notebook.tabs());
        for tab_id in current_tabs:
            if self.log_notebook.index(tab_id) != 0: self.log_notebook.forget(tab_id)
        main_log_widget = self.uid_log_widgets.get('main'); self.uid_log_widgets.clear()
        if main_log_widget:
            self.uid_log_widgets['main'] = main_log_widget
            try: main_log_widget.config(state=tk.NORMAL); main_log_widget.delete('1.0', tk.END); main_log_widget.config(state=tk.DISABLED)
            except Exception as e: print(f"Error clearing main log: {e}")
        for uid in uids_to_monitor:
            uid_str = str(uid); tab_frame = ttk.Frame(self.log_notebook, padding=2, style='TFrame')
            tab_text = f"UID: {uid_str}"; self.log_notebook.add(tab_frame, text=tab_text)
            log_text_widget = scrolledtext.ScrolledText(tab_frame, wrap=tk.WORD, state=tk.DISABLED, bd=1, relief=tk.SOLID, bg=BG_WIDGET_ALT, fg=FG_TEXT_DARK, insertbackground=FG_TEXT_DARK, font=self.log_font, borderwidth=1, highlightthickness=1, highlightcolor=ACCENT_BRIGHT_BLUE, highlightbackground=BORDER_LIGHT);
            log_text_widget.pack(fill=tk.BOTH, expand=True); self.uid_log_widgets[uid_str] = log_text_widget

    def _update_tab_text(self, uid_str, new_text):
        """在主线程中安全地更新Notebook标签页的文本"""
        # ... (代码保持不变) ...
        try:
            target_widget_id = None
            # 可能需要检查 notebook 是否还存在
            if not self.log_notebook.winfo_exists(): return
            for tab_id in self.log_notebook.tabs():
                current_text = self.log_notebook.tab(tab_id, "text")
                if current_text == f"UID: {uid_str}" or current_text == f"{new_text} ({uid_str})": target_widget_id = tab_id; break
            if target_widget_id: self.log_notebook.tab(target_widget_id, text=f"{new_text} ({uid_str})")
        except tk.TclError as e: print(f"Error updating tab text for UID {uid_str}: {e}")
        except Exception as e: print(f"Unexpected error updating tab text for UID {uid_str}: {e}")

    def _run_backend_process(self, target_uids_list, max_initial_likes, polling_interval_seconds, session, csrf_token, log_queue, stop_event):
        """后台核心工作线程，处理多个UID，使用Wbi签名，并添加计时"""
        # ... (代码保持不变，已包含 Wbi 和计时逻辑) ...
        latest_dynamic_ids = {}; uid_to_uname = {}; error_occurred = False; stop_message_sent = False
        try:
            phase1_start_time = time.time(); _log_message(log_queue, f"--- Phase 1: 开始高速扫描 UIDs: {','.join(target_uids_list)} (检查首页) ---", target_uid='main'); initial_dynamics_to_like = []; processed_dynamic_ids_initial = set(); uid_scan_delay_min = 0.8; uid_scan_delay_max = 2.0
            for index, current_target_uid in enumerate(target_uids_list):
                if stop_event.is_set(): _log_message(log_queue, f"初始扫描中断。", target_uid='main'); break
                _log_message(log_queue, f"--- 开始检查首页动态 ---", target_uid=current_target_uid)
                dynamics_batch, _, _, host_uname = get_up_dynamics(session, current_target_uid, "", log_queue, stop_event)
                if host_uname and host_uname != f"UID_{current_target_uid}": uid_to_uname[current_target_uid] = host_uname; self.root.after(0, self._update_tab_text, current_target_uid, host_uname)
                uname_display = uid_to_uname.get(current_target_uid, f"UID {current_target_uid}")
                if stop_event.is_set(): break
                if dynamics_batch is None: _log_message(log_queue, f"获取首页动态失败，跳过。", target_uid=current_target_uid); continue
                current_batch_latest_id = "0"
                if not dynamics_batch: _log_message(log_queue,f"首页未找到任何动态。", target_uid=current_target_uid)
                else:
                    _log_message(log_queue, f"获取到 {len(dynamics_batch)} 条首页动态，快速检查中...", target_uid=current_target_uid)
                    for dynamic_data in dynamics_batch:
                         dynamic_id = dynamic_data.get("dynamic_id", "0");
                         if dynamic_id > current_batch_latest_id: current_batch_latest_id = dynamic_id
                    latest_dynamic_ids[current_target_uid] = current_batch_latest_id
                    for dynamic_data in dynamics_batch:
                        if stop_event.is_set(): break
                        dynamic_id = dynamic_data.get("dynamic_id"); needs_like = dynamic_data.get("needs_like", False)
                        if not dynamic_id or dynamic_id in processed_dynamic_ids_initial: continue
                        processed_dynamic_ids_initial.add(dynamic_id)
                        if needs_like: initial_dynamics_to_like.append({'id': dynamic_id, 'uid': current_target_uid})
                _log_message(log_queue, f"检查完毕 (最新ID: {latest_dynamic_ids.get(current_target_uid, 'N/A')})", target_uid=current_target_uid)
                if stop_event.is_set(): break
                if len(target_uids_list) > 1 and index < len(target_uids_list) - 1: uid_wait = random.uniform(uid_scan_delay_min, uid_scan_delay_max); _log_message(log_queue, f"等待 {uid_wait:.1f} 秒...", target_uid='main'); stop_event.wait(timeout=uid_wait)
            scan_duration = time.time() - phase1_start_time; _log_message(log_queue, f"--- 初始扫描: 高速检查完成，共收集 {len(initial_dynamics_to_like)} 条待点赞动态，耗时 {scan_duration:.2f} 秒。---", target_uid='main')
            liked_count_actual = 0; initial_like_start_time = time.time()
            if not stop_event.is_set() and initial_dynamics_to_like:
                 _log_message(log_queue, f"--- 开始慢速点赞初始动态 (上限: {max_initial_likes}) ---", target_uid='main'); like_delay_min=4.0; like_delay_max=8.0
                 for i, like_info in enumerate(initial_dynamics_to_like):
                     dyn_id = like_info['id']; owner_uid = like_info['uid']; uname_display = uid_to_uname.get(owner_uid, f"UID {owner_uid}")
                     if liked_count_actual >= max_initial_likes: _log_message(log_queue, f"初始点赞已达到上限 ({max_initial_likes})。", target_uid='main'); break
                     if stop_event.is_set(): _log_message(log_queue, "初始点赞被中断。", target_uid='main'); break
                     _log_message(log_queue, f"点赞 ({liked_count_actual + 1}/{max_initial_likes}): {uname_display} 的动态 ID {dyn_id}", target_uid=owner_uid)
                     like_success = like_dynamic(session, dyn_id, csrf_token, log_queue, stop_event, target_uid=owner_uid)
                     if stop_event.is_set(): break
                     if like_success: liked_count_actual += 1
                     like_wait = random.uniform(like_delay_min, like_delay_max); _log_message(log_queue, f"    ...等待 {like_wait:.1f} 秒...", target_uid=owner_uid); stop_event.wait(timeout=like_wait)
                 initial_like_duration = time.time() - initial_like_start_time; _log_message(log_queue, f"--- 初始点赞处理完成，实际成功点赞 {liked_count_actual} 条，耗时 {initial_like_duration:.2f} 秒。 ---", target_uid='main')
            elif not stop_event.is_set(): _log_message(log_queue, "--- 初始扫描: 未收集到需要点赞的动态。 ---", target_uid='main')
            phase1_duration = time.time() - phase1_start_time
            if not stop_event.is_set(): _log_message(log_queue, f"--- 初始扫描阶段彻底完成 (总耗时: {phase1_duration:.2f} 秒) ---", target_uid='main')
            else: return
            _log_message(log_queue, f"--- Phase 2: 进入监控模式 (间隔: {polling_interval_seconds:.1f} 秒) ---", target_uid='main'); processed_dynamic_ids_monitor = processed_dynamic_ids_initial
            while not stop_event.is_set():
                wait_time = polling_interval_seconds * random.uniform(0.8, 1.2); _log_message(log_queue, f"监控: 等待 {wait_time:.1f} 秒...", target_uid='main'); stop_event.wait(timeout=wait_time);
                if stop_event.is_set(): break
                new_dynamics_this_cycle = []; _log_message(log_queue, f"监控: 开始检查 {len(target_uids_list)} 个UP主...", target_uid='main')
                uid_check_delay_min = 1.5; uid_check_delay_max = 3.5; check_start_time = time.time()
                for index, current_target_uid in enumerate(target_uids_list):
                    if stop_event.is_set(): break
                    last_seen_id = latest_dynamic_ids.get(current_target_uid, "0"); uname_display = uid_to_uname.get(current_target_uid, f"UID {current_target_uid}")
                    _log_message(log_queue, f"检查 {uname_display} (上次ID: {last_seen_id})", target_uid=current_target_uid)
                    dynamics_latest_batch, _, _, host_uname_latest = get_up_dynamics(session, current_target_uid, "", log_queue, stop_event)
                    if host_uname_latest and host_uname_latest != f"UID_{current_target_uid}": uid_to_uname[current_target_uid] = host_uname_latest; uname_display = host_uname_latest; self.root.after(0, self._update_tab_text, current_target_uid, host_uname_latest)
                    if stop_event.is_set(): break
                    if dynamics_latest_batch is None: _log_message(log_queue, f"获取最新动态失败。", target_uid=current_target_uid); continue
                    current_check_latest_id = "0"; found_new_for_this_uid = False
                    for dynamic_data in dynamics_latest_batch:
                        dynamic_id = dynamic_data.get("dynamic_id", "0");
                        if not dynamic_id or dynamic_id == "0": continue
                        if dynamic_id > current_check_latest_id: current_check_latest_id = dynamic_id
                        if dynamic_id > last_seen_id and dynamic_id not in processed_dynamic_ids_monitor:
                            needs_like = dynamic_data.get("needs_like", False);
                            if needs_like: new_dynamics_this_cycle.append({'id': dynamic_id, 'uid': current_target_uid, 'uname': uname_display}); found_new_for_this_uid = True; _log_message(log_queue, f"发现新动态 -> {dynamic_data.get('desc_text')}", target_uid=current_target_uid)
                            processed_dynamic_ids_monitor.add(dynamic_id)
                    if current_check_latest_id > last_seen_id: _log_message(log_queue, f"更新最新动态 ID 为 {current_check_latest_id}", target_uid=current_target_uid); latest_dynamic_ids[current_target_uid] = current_check_latest_id
                    if len(target_uids_list) > 1 and index < len(target_uids_list) - 1: uid_wait = random.uniform(uid_check_delay_min, uid_check_delay_max); stop_event.wait(timeout=uid_wait)
                check_duration = time.time() - check_start_time; _log_message(log_queue, f"监控: 本轮检查完毕，耗时 {check_duration:.2f} 秒。", target_uid='main')
                if stop_event.is_set(): break
                if new_dynamics_this_cycle:
                    monitor_like_start_time = time.time(); _log_message(log_queue, f"监控: 本轮共发现 {len(new_dynamics_this_cycle)} 条新动态，开始慢速点赞...", target_uid='main')
                    liked_in_monitor_batch = 0; monitor_like_delay_min = 4.0; monitor_like_delay_max = 8.0
                    for like_info in reversed(new_dynamics_this_cycle):
                        dyn_id = like_info['id']; owner_uid = like_info['uid']; uname_display = like_info['uname']
                        if stop_event.is_set(): break
                        _log_message(log_queue, f"尝试点赞 {uname_display} 的新动态 ID: {dyn_id}", target_uid=owner_uid)
                        like_success = like_dynamic(session, dyn_id, csrf_token, log_queue, stop_event, target_uid=owner_uid)
                        if stop_event.is_set(): break
                        if like_success: liked_in_monitor_batch += 1
                        monitor_like_wait = random.uniform(monitor_like_delay_min, monitor_like_delay_max); _log_message(log_queue, f"    ...等待 {monitor_like_wait:.1f} 秒...", target_uid=owner_uid); stop_event.wait(timeout=monitor_like_wait)
                    monitor_like_duration = time.time() - monitor_like_start_time
                    if not stop_event.is_set(): _log_message(log_queue, f"监控: 本轮点赞完成，成功 {liked_in_monitor_batch} 条，耗时 {monitor_like_duration:.2f} 秒。", target_uid='main')
                else: _log_message(log_queue, "监控: 本轮未发现需点赞的新动态。", target_uid='main')
        except RuntimeError as e: _log_message(log_queue, f"严重运行时错误: {e}。线程终止。", target_uid='main'); error_occurred = True; traceback.print_exc()
        except Exception as e: _log_message(log_queue, f"后台线程发生意外错误: {e}", target_uid='main'); log_queue.put({'target':'main', 'message':traceback.format_exc()}); error_occurred = True
        finally:
            if not stop_message_sent:
                stop_msg = {'target':'main', 'message': ""};
                if stop_event.is_set(): stop_msg['message'] = "BACKEND_STOPPED_MANUAL"
                elif error_occurred: stop_msg['message'] = "BACKEND_STOPPED_ERROR"
                else: stop_msg['message'] = "BACKEND_STOPPED_MANUAL"
                log_queue.put(stop_msg); stop_message_sent = True

    def _on_closing(self):
        """处理主窗口关闭事件"""
        # ... (代码保持不变) ...
        should_exit = True
        if self.is_running: should_exit = messagebox.askyesno("确认退出", "点赞/监控任务仍在运行中，确定要停止并退出吗？", parent=self.root)
        if should_exit:
            _log_message(self.log_queue, "收到退出请求，正在停止后台任务...")
            self.stop_event.set(); self.login_stop_event.set(); self._close_qr_window()
            if self.backend_thread and self.backend_thread.is_alive():
                 if threading.current_thread() != self.backend_thread: self.backend_thread.join(timeout=1.5)
                 else: print("Warning: _on_closing called from backend thread?")
            try: self.root.destroy()
            except tk.TclError as e: print(f"Error destroying main window: {e}")

# --- 程序主入口 ---
if __name__ == "__main__":
    # ... (代码保持不变) ...
    print("="*40); print("提示: 确保已安装 pip install requests qrcode Pillow brotli"); print("="*40 + "\n")
    root = None
    try:
        root = tk.Tk()
        app = BiliLikerApp(root)
        root.mainloop()
    except Exception as e: print(f"\nGUI启动或运行期间发生未捕获的严重错误: {e}"); traceback.print_exc()
    finally: print("程序执行完毕。")