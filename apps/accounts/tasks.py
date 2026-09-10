"""Tarefas assíncronas da app de contas."""

from __future__ import annotations

import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger("suabarbearia.celery")


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_password_reset_email(self, user_id: int, token: str) -> str:
    """Envia o e-mail com o link de redefinição de senha."""
    from apps.accounts.models import User

    user = User.objects.filter(pk=user_id, is_active=True).first()
    if user is None:
        logger.warning("Reset de senha solicitado para usuário inexistente: %s", user_id)
        return "user-not-found"

    link = f"{settings.FRONTEND_RESET_PASSWORD_URL}?token={token}"
    subject = "Sua Barbearia - Redefinição de senha"
    message = (
        f"Olá, {user.first_name}!\n\n"
        "Recebemos uma solicitação para redefinir a senha da sua conta na Sua Barbearia.\n"
        f"Use o link abaixo (válido por 2 horas):\n\n{link}\n\n"
        "Se não foi você, ignore este e-mail — sua senha continua a mesma.\n\n"
        "Equipe Sua Barbearia"
    )

    try:
        send_mail(
            subject,
            message,
            settings.DEFAULT_FROM_EMAIL,
            [user.email],
            fail_silently=False,
        )
    except Exception as exc:  # pragma: no cover - depende do provedor de e-mail
        logger.exception("Falha ao enviar e-mail de redefinição de senha.")
        raise self.retry(exc=exc) from exc

    logger.info("E-mail de redefinição enviado para o usuário %s", user_id)
    return "sent"
