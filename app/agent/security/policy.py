# -*- coding: utf-8 -*-
"""
安全策略 - 兼容性重导出

实际实现已移至 app/core/security/policy.py
保留此文件以确保向后兼容。
"""

from app.core.security.policy import SecurityPolicy, RiskLevel

__all__ = ["SecurityPolicy", "RiskLevel"]
