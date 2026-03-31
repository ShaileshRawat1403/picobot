"""Message bus module for decoupled channel-agent communication."""

from picobot.bus.events import InboundMessage, OutboundMessage
from picobot.bus.queue import MessageBus

__all__ = ["MessageBus", "InboundMessage", "OutboundMessage"]
