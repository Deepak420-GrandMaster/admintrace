"""Chat model access.

The rest of the system asks for a provider and never learns which one it got.
Adding a provider means adding a class here, not touching retrieval, answering
or the interface.
"""

from app.llm.base import ChatMessage, ChatProvider, ProviderError
from app.llm.registry import get_chat_provider

__all__ = ["ChatMessage", "ChatProvider", "ProviderError", "get_chat_provider"]
