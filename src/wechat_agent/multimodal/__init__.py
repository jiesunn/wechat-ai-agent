"""多模态占位：处理器协议、注册表、默认降级与路由。"""

from wechat_agent.multimodal.handlers import (
    MEDIA_TYPES,
    DefaultFallbackHandler,
    MediaHandler,
    MediaHandlerRegistry,
)
from wechat_agent.multimodal.router import MultimodalRouter

__all__ = [
    "MEDIA_TYPES",
    "DefaultFallbackHandler",
    "MediaHandler",
    "MediaHandlerRegistry",
    "MultimodalRouter",
]
