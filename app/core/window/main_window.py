# -*- coding: utf-8 -*-
"""
Miniblink 浏览器 - 主窗口
"""

import ctypes
import http.server
import logging
import mimetypes
import os
import pathlib
import random
import socket
import socketserver
import sys
import threading
import time
from ctypes import wintypes, byref

logger = logging.getLogger(__name__)

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

user32.SetLayeredWindowAttributes.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_ubyte, ctypes.c_uint]
user32.SetLayeredWindowAttributes.restype = ctypes.c_bool

LWA_ALPHA = 0x00000002
LWA_COLORKEY = 0x00000001

from ..bridge.miniblink_bridge import MiniBlinkBridge
from ..bus.event_bus import event_bus
from ..bus.events import EventType
from ..bus.event_models import FadeOutEvent
from ..handler.message_handler import MessageHandler
from ..util.components_loader import load_component_config
from ..di.container import get_container

WM_COMMAND = 0x0111
WM_CLOSE = 0x10
WM_SYSCOMMAND = 0x112
SC_MINIMIZE = 0xF020
SC_MAXIMIZE = 0xF030
SC_RESTORE = 0xF120
SC_CLOSE = 0xF060

BN_CLICKED = 0

class POINT(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_long),
        ("y", ctypes.c_long),
    ]

class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]

class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_uint),
        ("flags", ctypes.c_uint),
        ("showCmd", ctypes.c_uint),
        ("ptMinPosition", POINT),
        ("ptMaxPosition", POINT),
        ("rcNormalPosition", RECT),
    ]

def _get_project_root():
    """获取项目根目录"""
    if "__compiled__" in globals():
        return pathlib.Path(__file__).resolve().parent.parent.parent.parent
    elif getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        return pathlib.Path(sys._MEIPASS)
    else:
        return pathlib.Path(sys.argv[0] if sys.argv[0] else __file__).resolve().parent


class StaticServer:
    """静态文件服务器 - 支持 SPA 路由回退"""

    _instance = None
    _server = None
    _thread = None
    _port = None

    DEFAULT_PORTS = [8080, 8081, 8082, 8083, 8084, 8085, 8086, 8087, 8088, 8089]
    PORT_RANGE = range(8000, 9000)

    def __new__(cls, directory):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        cls._instance.directory = directory
        return cls._instance

    @classmethod
    def get_instance(cls, directory=None):
        """获取单例实例"""
        if cls._instance is None and directory:
            cls._instance = cls(directory)
        return cls._instance

    @classmethod
    def get_url(cls):
        """获取服务器 URL"""
        if cls._port:
            return f"http://localhost:{cls._port}/index.html"
        return None

    @classmethod
    def is_port_available(cls, port: int) -> bool:
        """检查端口是否可用"""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('', port))
                return True
        except OSError:
            return False

    @classmethod
    def find_available_port(cls, preferred_ports=None, max_attempts=10) -> int:
        """查找可用端口

        Args:
            preferred_ports: 优先尝试的端口列表
            max_attempts: 最大尝试次数

        Returns:
            可用的端口号
        """
        if preferred_ports is None:
            preferred_ports = cls.DEFAULT_PORTS

        tried_ports = set()

        for port in preferred_ports:
            if port not in tried_ports and cls.is_port_available(port):
                tried_ports.add(port)
                return port

        for _ in range(max_attempts):
            port = random.randint(8000, 8999)
            if port not in tried_ports and cls.is_port_available(port):
                tried_ports.add(port)
                return port

        raise OSError(f"无法找到可用端口，已尝试: {tried_ports}")

    def start(self, port=0, max_retries=3):
        """启动服务器

        Args:
            port: 端口号，0 表示由系统自动分配，-1 表示使用首选端口列表
            max_retries: 最大重试次数

        Returns:
            实际使用的端口号
        """
        mimetypes.init()
        mimetypes.add_type('application/javascript', '.js')
        mimetypes.add_type('text/css', '.css')
        mimetypes.add_type('application/json', '.json')
        mimetypes.add_type('image/svg+xml', '.svg')
        mimetypes.add_type('application/wasm', '.wasm')

        os.chdir(self.directory)

        actual_port = port
        retry_count = 0

        while retry_count < max_retries:
            try:
                if port == 0:
                    actual_port = self.find_available_port()
                elif port == -1:
                    actual_port = self.find_available_port(self.DEFAULT_PORTS)
                elif not self.is_port_available(port):
                    logger.warning(f"端口 {port} 已被占用，尝试查找其他端口")
                    actual_port = self.find_available_port()
                    port = actual_port

                self._server = socketserver.ThreadingTCPServer(
                    ("", actual_port), self._create_handler()
                )
                self._port = actual_port
                logger.info(f"静态服务器启动: http://localhost:{actual_port}")
                break
            except OSError as e:
                retry_count += 1
                if port != 0:
                    logger.warning(f"端口 {port} 占用 ({retry_count}/{max_retries}): {e}")
                    actual_port = self.find_available_port()
                    port = actual_port
                else:
                    raise e
        else:
            raise OSError(f"无法启动服务器，已重试 {max_retries} 次")

        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True
        )
        self._thread.start()

        return actual_port
    
    def _create_handler(self):
        """创建请求处理器"""
        directory = self.directory
        
        class SPAHandler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=directory, **kwargs)

            def do_GET(self):
                """处理 GET 请求，支持 SPA 路由回退"""
                path = self.path
                if path == '/':
                    path = '/index.html'

                if '?' in path:
                    path = path.split('?')[0]

                file_path = self.translate_path(path)

                if not os.path.exists(file_path):
                    logger.debug(f"文件不存在，回退到 index.html: {path}")
                    self.path = '/index.html'

                return super().do_GET()
            
            def log_message(self, format, *args):
                """自定义日志格式"""
                logger.debug(f"[HTTP] {args[0]}")
        
        return SPAHandler
    
    def stop(self):
        """停止服务器"""
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            logger.info("静态服务器已停止")
            self._server = None
            self._port = None


class MainWindow:

    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        self._initialized = True
        
        self.project_root = _get_project_root()
        
        self.dll_dir = str(self.project_root / "dll")
        self.dll_path = str(self.project_root / "dll" / "mb132_x64.dll")
        self.html_dir = str(self.project_root / "html")
        self.html_path = str(self.project_root / "html" / "index.html")
        self.lib = None
        self.webview = None
        self.hwnd = None
        self.running = True
        self.title = "Python MiniBlink"
        
        self._wnd_proc = None
        self._wnd_class = None
        
        self.bridge = None
        self.app_icon = None
        
        self.calculator = None
        
        config = load_component_config()
        window_config = config.get('window', {})
        self.window_width = window_config.get('width', 800)
        self.window_height = window_config.get('height', 600)
        self.should_remove_titlebar = window_config.get('remove_titlebar', False)
        self.resizable_border = window_config.get('resizable_border', False)
    
    def _get_component(self, component_type):
        """从 DI 容器获取组件
        
        Args:
            component_type: 组件类型
            
        Returns:
            组件实例，如果未注册返回 None
        """
        try:
            container = get_container()
            if container.has(component_type):
                return container.resolve(component_type)
        except Exception as e:
            logger.warning(f"获取组件失败 {component_type.__name__}: {e}")
        return None
    
    @classmethod
    def get_instance(cls):
        """获取 MainWindow 单例实例"""
        return cls._instance
    
    def load_window_icon(self):
        """加载并设置窗口图标"""
        try:
            if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
                icon_path = os.path.join(self.dll_dir, "app_icon.ico")
            else:
                icon_path = os.path.join(self.project_root, "app_icon.ico")
            
            if os.path.exists(icon_path):
                self.app_icon = user32.LoadImageW(None, icon_path, 1, 32, 32, 16)
                if self.app_icon:
                    user32.SendMessageW(self.hwnd, 0x80, 1, self.app_icon)
                    user32.SendMessageW(self.hwnd, 0x80, 0, self.app_icon)
                    logger.info(f"窗口图标已设置: {icon_path}")
                else:
                    logger.warning(f"加载图标失败，错误码: {kernel32.GetLastError()}")
            else:
                logger.warning(f"图标文件不存在: {icon_path}")
        except Exception as e:
            logger.error(f"设置窗口图标失败: {e}")
    
    def start_static_server(self, port=0):
        """启动静态文件服务器
        
        Args:
            port: 端口号，0 表示由系统自动分配
            
        Returns:
            实际使用的端口号
        """
        self._static_server = StaticServer.get_instance(self.html_dir)
        actual_port = self._static_server.start(port=port)
        self._html_url = f"http://localhost:{actual_port}/index.html"
        logger.info(f"静态服务器 URL: {self._html_url}")
        return actual_port
    
    def load_html_from_server(self):
        """从本地静态服务器加载 HTML"""
        if not self._html_url:
            logger.error("静态服务器未启动")
            return False
        
        try:
            # 使用 mbLoadHtmlWithBaseUrl 加载本地服务器 URL
            html_path = os.path.join(self.html_dir, 'index.html')
            with open(html_path, 'r', encoding='utf-8') as f:
                html_content = f.read()
            base_url = self._html_url.replace('/index.html', '/')
            self.lib.mbLoadHtmlWithBaseUrl(
                self.webview, 
                html_content.encode('utf-8'), 
                base_url.encode('utf-8')
            )
            logger.info(f"已加载 HTML: {base_url}")
            return True
        except Exception as e:
            logger.error(f"加载 HTML 失败: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _window_procedure(self, hwnd, msg, wparam, lparam):
        """窗口过程 - 处理关闭消息"""
        if msg == WM_CLOSE:
            logger.info("收到 WM_CLOSE，停止应用程序...")
            self.fade_out(duration=300)
            self.running = False
            # 停止静态服务器
            if self._static_server:
                self._static_server.stop()
            try:
                self.lib.mbDestroyWebWindow(self.webview)
            except Exception as e:
                logger.warning("[MainWindow] 销毁 WebWindow 失败: %s", e)
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
        
    def _init_dll_functions(self):
        functions = {
            'mbSetMbDllPath': ([wintypes.LPCWSTR], None),
            # 'mbSetMbMainDllPath': ([wintypes.LPCWSTR], None),
            'mbInit': ([ctypes.c_void_p], None),
            'mbCreateWebWindow': (
                [ctypes.c_int, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int],
                ctypes.c_void_p
            ),
            'mbDestroyWebWindow': ([ctypes.c_void_p], None),
            'mbShowWindow': ([ctypes.c_void_p, ctypes.c_bool], None),
            'mbMoveToCenter': ([ctypes.c_void_p], None),
            'mbGetHostHWND': ([ctypes.c_void_p], wintypes.HWND),
            'mbLoadHtmlWithBaseUrl': ([ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p], None),
            'mbOnAlertBox': ([ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p], None),
            'mbOnNavigation': ([ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p], None),
            'mbRunJs': ([ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p, 
                        ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p], None),
            'mbWebFrameGetMainFrame': ([ctypes.c_void_p], ctypes.c_void_p),
            'mbResponseQuery': ([ctypes.c_void_p, ctypes.c_int64, ctypes.c_int, ctypes.c_char_p], None),
            'mbOnJsQuery': ([ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p], None),
        }
        
        for name, (argtypes, restype) in functions.items():
            try:
                func = getattr(self.lib, name)
                func.argtypes = argtypes
                if restype:
                    func.restype = restype
            except AttributeError:
                pass
    
    def load_dll(self):
        if not os.path.exists(self.dll_path):
            logger.error(f"未找到 DLL: {self.dll_path}")
            return False
        
        try:
            self.lib = ctypes.WinDLL(self.dll_path)
            self._init_dll_functions()
            logger.info(f"DLL 已加载: {self.dll_path}")
            return True
        except Exception as e:
            logger.error(f"DLL 加载失败: {e}")
            return False
    
    def init_engine(self):
        try:
            mbInit = self.lib.mbInit
            mbInit.argtypes = [ctypes.c_void_p]
            mbInit(None)
            
            if hasattr(self.lib, 'mbEnableHighDPISupport'):
                self.lib.mbEnableHighDPISupport()
                logger.info("高 DPI 支持已启用")
            else:
                logger.debug("mbEnableHighDPISupport 函数不可用")
            
            logger.info("引擎已初始化")
            return True
        except Exception as e:
            logger.error(f"初始化失败: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def create_window(self):
        try:
            self.webview = self.lib.mbCreateWebWindow(
                0, None, 100, 100, self.window_width, self.window_height
            )
            if not self.webview:
                logger.error("[ERROR] 创建窗口失败")
                return False
            
            self.hwnd = self.lib.mbGetHostHWND(self.webview)
            
            if self.hwnd:
                user32.SetWindowTextW(self.hwnd, "Python MiniBlink")
                
                GWL_EXSTYLE = -20
                GWL_HWNDPARENT = -8
                WS_EX_APPWINDOW = 0x00040000
                WS_EX_TOOLWINDOW = 0x00000080
                WS_EX_LAYERED = 0x00080000
                
                current_style = user32.GetWindowLongPtrW(self.hwnd, GWL_EXSTYLE)
                current_style &= ~WS_EX_TOOLWINDOW
                current_style |= WS_EX_APPWINDOW
                user32.SetWindowLongPtrW(self.hwnd, GWL_EXSTYLE, current_style)
                
                owner = user32.GetWindowLongPtrW(self.hwnd, GWL_HWNDPARENT)
                if owner:
                    user32.SetWindowLongPtrW(self.hwnd, GWL_HWNDPARENT, None)
                
                shell32 = ctypes.windll.shell32
                try:
                    shell32.SetCurrentProcessExplicitAppUserModelIDW("PythonMiniBlink.Browser")
                    logger.info("AppUserModelID 已设置")
                except Exception as e:
                    logger.warning(f"设置 AppUserModelID 失败: {e}")

                user32.ShowWindow(self.hwnd, 0)
                logger.info("窗口已创建（初始隐藏）")
            return True
        except Exception as e:
            logger.error(f"创建窗口失败: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def fade_out(self, duration=300):
        """淡出动画效果
        
        Args:
            duration: 动画持续时间（毫秒）
        """
        if not self.hwnd:
            return
        
        GWL_EXSTYLE = -20
        WS_EX_LAYERED = 0x00080000
        LWA_ALPHA = 0x00000002
        
        current_style = user32.GetWindowLongPtrW(self.hwnd, GWL_EXSTYLE)
        was_layered = current_style & WS_EX_LAYERED
        
        if not was_layered:
            user32.SetWindowLongPtrW(self.hwnd, GWL_EXSTYLE, current_style | WS_EX_LAYERED)
        
        steps = 20
        step_duration = duration / steps
        
        for i in range(steps):
            alpha = 255 - int((255 / steps) * (i + 1))
            if alpha < 0:
                alpha = 0
            
            user32.SetLayeredWindowAttributes(self.hwnd, 0, alpha, LWA_ALPHA)
            time.sleep(step_duration / 1000.0)
        
        user32.SetLayeredWindowAttributes(self.hwnd, 0, 0, LWA_ALPHA)
        user32.ShowWindow(self.hwnd, 0)
        
        logger.info("[INFO] 淡出动画完成")
    
    def _on_fade_out(self, event: FadeOutEvent):
        """处理淡出事件"""
        logger.info("[INFO] 收到淡出事件")
        self.fade_out(duration=300)
        self.running = False
        # 停止静态服务器
        if self._static_server:
            self._static_server.stop()
        try:
            self.lib.mbDestroyWebWindow(self.webview)
        except Exception as e:
            logger.warning("[MainWindow] 销毁 WebWindow 失败: %s", e)
        user32.PostQuitMessage(0)
    
    def load_html_with_buttons(self):
        """加载主 HTML 页面"""
        try:
            with open(self.html_path, 'r', encoding='utf-8') as f:
                html = f.read()
            logger.info("HTML 页面已加载")
        except Exception as e:
            logger.error(f"加载 HTML 文件失败: {e}")
            return False
        
        try:
            self.lib.mbLoadHtmlWithBaseUrl(self.webview, html.encode('utf-8'), b"about:blank")
            return True
        except Exception as e:
            logger.error(f"加载 HTML 到 MiniBlink 失败: {e}")
            return False
    
    def run_message_loop(self):
        logger.info("启动消息循环...")
        try:
            msg = wintypes.MSG()
            while self.running:
                ret = user32.GetMessageW(byref(msg), None, 0, 0)
                if ret == 0:
                    break
                elif ret == -1:
                    logger.error("GetMessageW 错误")
                    break
                user32.TranslateMessage(byref(msg))
                user32.DispatchMessageW(byref(msg))
                
                if self.hwnd:
                    if not user32.IsWindow(self.hwnd):
                        logger.info("窗口已关闭，退出...")
                        break
        except Exception as e:
            logger.error(f"消息循环错误: {e}")
        logger.info("消息循环已退出")
    
    def remove_titlebar(self):
        if not self.hwnd:
            return
        
        try:
            GWL_STYLE = -16
            WS_CAPTION = 0x00C00000
            WS_THICKFRAME = 0x00040000
            
            current_style = user32.GetWindowLongPtrW(self.hwnd, GWL_STYLE)
            
            if self.resizable_border:
                new_style = current_style & ~WS_CAPTION
            else:
                new_style = current_style & ~WS_CAPTION & ~WS_THICKFRAME
            
            user32.SetWindowLongPtrW(self.hwnd, GWL_STYLE, new_style)
            
            SWP_FRAMECHANGED = 0x0020
            user32.SetWindowPos(self.hwnd, None, 0, 0, 0, 0,
                              SWP_FRAMECHANGED | 0x0010 | 0x0004 | 0x0001 | 0x0002)
            
            user32.ShowWindow(self.hwnd, 3)
        except Exception as e:
            logger.error(f"移除标题栏失败: {e}")
    
    def run(self):
        
        if not self.load_dll():
            return
        
        if not self.init_engine():
            return
        
        if not self.create_window():
            return
        
        self.load_window_icon()
        
        # 初始化桥接模块
        self.bridge = MiniBlinkBridge(self)
        
        # 初始化消息处理器组件
        self.message_handler = MessageHandler(self.hwnd)
        
        # 订阅事件
        event_bus.subscribe(EventType.FADE_OUT, self._on_fade_out)
        
        # 设置所有回调
        self.bridge.setup_all_callbacks()
        
        # 启动静态文件服务器并加载 HTML
        self.start_static_server()
        self.load_html_from_server()
        
        if self.should_remove_titlebar:
            self.remove_titlebar()

        user32.ShowWindow(self.hwnd, 1)
        user32.SetForegroundWindow(self.hwnd)
        
        
        try:
            self.lib.mbShowWindow(self.webview, True)
            self.lib.mbMoveToCenter(self.webview)
        except Exception as e:
            logger.error(f"显示窗口失败: {e}")
        
        # 窗口就绪后加载组件
        from app.core.util import load_components
        from app.core.di.container import get_container
        container = get_container()
        load_components(container)
           
        self.run_message_loop()
