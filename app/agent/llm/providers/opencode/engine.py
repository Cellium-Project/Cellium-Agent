# -*- coding: utf-8 -*-
import uuid
from typing import Dict, Optional

from ...engine import OpenAICompatibleEngine
from ...transport import OpenAICompatTransport


class _SessionTransport(OpenAICompatTransport):
    """注入 X-Opencode-Session 的 transport 子类"""

    def __init__(self, session_id: str, **kwargs):
        super().__init__(**kwargs)
        self._session_id = session_id

    @property
    def _headers(self) -> Dict[str, str]:
        h = super()._headers
        h["X-Opencode-Session"] = self._session_id
        return h


class OpenCodeEngine(OpenAICompatibleEngine):
    """OpenCode 引擎，注入 X-Opencode-Session header"""

    def _ensure_transport(self):
        if self._transport is None:
            self._transport = _SessionTransport(
                session_id=self._extra_client_args.get("session_id") or uuid.uuid4().hex,
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout,
            )
        return self._transport
