"""Utilitários compartilhados."""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

from django.utils import timezone


def combine_local(day: date, moment: time) -> datetime:
    """Combina data + hora no fuso configurado, retornando datetime aware."""
    naive = datetime.combine(day, moment)
    return timezone.make_aware(naive, timezone.get_current_timezone())


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """Retorna o intervalo aware [00:00, 23:59:59.999999] do dia informado."""
    start = combine_local(day, time.min)
    end = combine_local(day, time.max)
    return start, end


def add_minutes(moment: time, minutes: int) -> time:
    """Soma minutos a um `time`, com wrap-around no mesmo dia."""
    base = datetime.combine(date(2000, 1, 1), moment) + timedelta(minutes=minutes)
    return base.time()


def time_to_minutes(moment: time) -> int:
    return moment.hour * 60 + moment.minute


def minutes_to_time(total: int) -> time:
    total = total % (24 * 60)
    return time(hour=total // 60, minute=total % 60)


def only_digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def normalize_phone(value: str | None) -> str:
    """Normaliza telefone brasileiro para apenas dígitos (com DDD)."""
    return only_digits(value)


def is_valid_cnpj(value: str | None) -> bool:
    """Valida CNPJ pelos dígitos verificadores."""
    cnpj = only_digits(value)
    if len(cnpj) != 14 or cnpj == cnpj[0] * 14:
        return False

    def check_digit(base: str, weights: list[int]) -> str:
        total = sum(int(d) * w for d, w in zip(base, weights, strict=True))
        remainder = total % 11
        return "0" if remainder < 2 else str(11 - remainder)

    first = check_digit(cnpj[:12], [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    second = check_digit(cnpj[:13], [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    return cnpj[12] == first and cnpj[13] == second


def is_valid_cpf(value: str | None) -> bool:
    """Valida CPF pelos dígitos verificadores."""
    cpf = only_digits(value)
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False

    def check_digit(base: str, start_weight: int) -> str:
        total = sum(int(d) * w for d, w in zip(base, range(start_weight, 1, -1), strict=True))
        remainder = (total * 10) % 11
        return "0" if remainder == 10 else str(remainder)

    first = check_digit(cpf[:9], 10)
    second = check_digit(cpf[:10], 11)
    return cpf[9] == first and cpf[10] == second
