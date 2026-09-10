"""Utilidades de logging: injeção do Request ID nos registros."""

from __future__ import annotations

import logging

from apps.core.context import get_request_id


class RequestIDFilter(logging.Filter):
    """Disponibiliza `%(request_id)s` para os formatters."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id() or "-"
        return True
