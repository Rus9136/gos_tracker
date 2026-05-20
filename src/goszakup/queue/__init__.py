"""Очередь задач: Dramatiq поверх Redis.

Импортируйте actors отсюда. Broker инициализируется при импорте `broker`
модуля (необходимое условие для Dramatiq autodiscovery).
"""

from __future__ import annotations

from .broker import broker

__all__ = ["broker"]
