"""多模态占位：处理器协议、注册表与默认降级。"""

from wechat_agent.multimodal.handlers import (
    MEDIA_TYPES,
    DefaultFallbackHandler,
    MediaHandler,
    MediaHandlerRegistry,
)

__all__ = [
    "MEDIA_TYPES",
    "DefaultFallbackHandler",
    "MediaHandler",
    "MediaHandlerRegistry",
]
