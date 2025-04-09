# login.py
import requests
import time
import qrcode
import os
from PIL import Image, ImageTk  # Import ImageTk for Tkinter
import threading
import queue # For communication with GUI

# --- Constants ---
QR_GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
QR_POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com/",
}
LOGIN_TIMEOUT = 180 # seconds

# --- Helper Function ---
def _log_message(log_queue, message):
    """Safely put log message into the queue for the GUI."""
    if log_queue:
        log_queue.put(message)
    else:
        print(message) # Fallback if no queue provided

# --- Modified Login Function ---
def login_via_qrcode(log_queue=None, qr_display_callback=None, stop_event=None):
    """
    Handles Bilibili QR Code login.

    Args:
        log_queue: A queue.Queue object to send log messages to the GUI.
        qr_display_callback: A function(image) called by this thread to display the QR code.
                             The function should handle displaying the PIL Image.
        stop_event: A threading.Event() object to signal early termination.

    Returns:
        A dictionary containing cookies ('SESSDATA', 'bili_jct', 'DedeUserID') on success,
        None on failure or cancellation.
    """
    session = requests.Session()
    session.headers.update(HEADERS)
    qrcode_key = None
    qr_image = None

    try:
        # 1. Get QR Code Info
        _log_message(log_queue, "正在获取登录二维码...")
        response_gen = session.get(QR_GENERATE_URL, timeout=10)
        response_gen.raise_for_status()
        data_gen = response_gen.json()

        if data_gen.get("code") != 0:
            _log_message(log_queue, f"获取二维码失败: {data_gen.get('message', '未知错误')}")
            return None

        qrcode_url = data_gen["data"]["url"]
        qrcode_key = data_gen["data"]["qrcode_key"]

        # 2. Generate QR Code Image (but don't show it directly)
        _log_message(log_queue, "正在生成二维码...")
        qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=4)
        qr.add_data(qrcode_url)
        qr.make(fit=True)
        qr_image = qr.make_image(fill_color="black", back_color="white")

        # 3. Use callback to display QR code in GUI (if provided)
        if qr_display_callback:
            _log_message(log_queue, "请扫描弹出的二维码进行登录...")
            qr_display_callback(qr_image) # GUI thread handles the display
        else:
             _log_message(log_queue, f"请在浏览器打开链接或使用其他方式扫描: {qrcode_url}")


        # 4. Poll QR Code Status
        _log_message(log_queue, "等待扫描和确认...")
        start_time = time.time()
        poll_interval = 2
        last_msg = ""

        while time.time() - start_time < LOGIN_TIMEOUT:
            # Check if GUI requested stop
            if stop_event and stop_event.is_set():
                _log_message(log_queue, "登录过程被用户取消。")
                return None

            time.sleep(poll_interval)
            try:
                params = {"qrcode_key": qrcode_key}
                response_poll = session.get(QR_POLL_URL, params=params, timeout=10)
                response_poll.raise_for_status()
                data_poll = response_poll.json()

                if "data" not in data_poll or "code" not in data_poll["data"]:
                    _log_message(log_queue, f"轮询响应格式异常: {data_poll}")
                    time.sleep(poll_interval * 2); continue

                code = data_poll["data"]["code"]
                message = data_poll["data"].get("message", "")

                if code == 0: # Success
                    _log_message(log_queue, "登录成功!")
                    cookies_dict = session.cookies.get_dict()
                    required = ["SESSDATA", "bili_jct", "DedeUserID"]
                    if all(k in cookies_dict for k in required):
                        _log_message(log_queue, "已获取必要的 Cookies。")
                        # Add full cookie string for potential use with headers
                        cookies_dict['full_cookie'] = "; ".join([f"{k}={v}" for k, v in session.cookies.items()])
                        return cookies_dict
                    else:
                        _log_message(log_queue, "登录成功但缺少关键 Cookies。")
                        return None # Treat as failure if critical cookies missing

                elif code == 86090: # Scanned, waiting confirmation
                    current_msg = "二维码已扫描，请在手机上确认登录..."
                    if current_msg != last_msg: _log_message(log_queue, current_msg); last_msg = current_msg
                elif code == 86101: # Not scanned
                    current_msg = "二维码未扫描，请尽快扫描..."
                    if current_msg != last_msg: _log_message(log_queue, current_msg); last_msg = current_msg
                    pass # Don't log repeatedly
                elif code == 86038: # Expired
                    _log_message(log_queue, f"二维码已过期 ({message})，请重试。")
                    return None
                else: # Other codes
                    current_msg = f"轮询状态: code={code}, message={message}"
                    if current_msg != last_msg: _log_message(log_queue, current_msg); last_msg = current_msg
                    time.sleep(poll_interval)

            except requests.exceptions.Timeout: _log_message(log_queue, "轮询请求超时，稍后重试..."); time.sleep(poll_interval * 2)
            except requests.exceptions.RequestException as e: _log_message(log_queue, f"轮询请求异常: {e}"); time.sleep(poll_interval * 2)
            except Exception as e: _log_message(log_queue, f"处理轮询响应时出错: {e}"); return None

        _log_message(log_queue, "登录超时，请重试。")
        return None

    except requests.exceptions.Timeout: _log_message(log_queue, "获取二维码请求超时。"); return None
    except requests.exceptions.RequestException as e: _log_message(log_queue, f"获取二维码网络请求错误: {e}"); return None
    except ImportError: _log_message(log_queue, "错误：缺少 Pillow 或 qrcode 库。"); return None
    except Exception as e: _log_message(log_queue, f"登录时发生未知错误: {e}"); traceback.print_exc(); return None
    finally:
        # Signal that the QR process is done, regardless of success/failure
        if qr_display_callback:
             # Send a special signal or let the GUI handle closing based on return value
             _log_message(log_queue, "LOGIN_PROCESS_FINISHED") # Use a signal

# --- Standalone test (optional) ---
if __name__ == '__main__':
    print("测试登录模块 (无GUI)...")
    test_queue = queue.Queue()
    stop_ev = threading.Event()
    # Simulate running in a thread for testing logging
    def run_test():
        cookies = login_via_qrcode(test_queue, stop_event=stop_ev)
        if cookies:
            test_queue.put("\n--- 登录成功 (测试) ---")
            test_queue.put(f"  bili_jct: {cookies.get('bili_jct')}")
        else:
            test_queue.put("\n--- 登录失败 (测试) ---")
    # Start the test thread
    test_thread = threading.Thread(target=run_test)
    test_thread.start()
    # Print messages from the queue
    while test_thread.is_alive():
        try:
            msg = test_queue.get(timeout=0.1)
            print(msg)
        except queue.Empty:
            pass
    # Print any remaining messages
    while not test_queue.empty(): print(test_queue.get_nowait())
    test_thread.join()