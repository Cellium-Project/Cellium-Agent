# -*- coding: utf-8 -*-
"""异常的可读描述（httpx 网络异常常为空 message）"""


def describe_error(exc: BaseException) -> str:
    """把异常转成用户可读文本，避免出现 "ReadError: " 这种空详情"""
    name = type(exc).__name__
    msg = str(exc).strip()
    try:
        import httpx
        if isinstance(exc, (
            httpx.ReadError, httpx.ReadTimeout,
            httpx.WriteError, httpx.WriteTimeout,
            httpx.ConnectError, httpx.ConnectTimeout,
            httpx.PoolTimeout, httpx.RemoteProtocolError,
        )):
            detail = msg or "连接被重置"
            return f"网络中断（{name}）：{detail}，请稍后重试"
    except Exception:
        pass
    if not msg:
        cause = exc.__cause__ or exc.__context__
        if cause is not None:
            msg = str(cause).strip() or type(cause).__name__
    return f"{name}: {msg}" if msg else name


def is_retryable_net_error(exc: BaseException) -> bool:
    try:
        import httpx
        return isinstance(exc, (
            httpx.ReadError, httpx.ReadTimeout,
            httpx.WriteError, httpx.WriteTimeout,
            httpx.ConnectError, httpx.ConnectTimeout,
            httpx.PoolTimeout, httpx.RemoteProtocolError,
        ))
    except Exception:
        return False
