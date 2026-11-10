# -*- coding: utf-8 -*-
"""core 与上层运行时之间的端口注册表"""

class RuntimePorts:
    task = None
    session = None
    channel = None
    memory = None
    delegated = None


PORTS = RuntimePorts()


def bind_ports(**ports):
    for name, value in ports.items():
        setattr(PORTS, name, value)
