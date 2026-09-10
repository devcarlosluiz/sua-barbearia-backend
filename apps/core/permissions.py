"""Permissões reutilizáveis baseadas no papel (role) do usuário.

Regra de ouro do projeto: nenhuma permissão é confiada ao frontend. Toda view
declara explicitamente quem pode acessá-la.
"""

from __future__ import annotations

from typing import Any

from rest_framework.permissions import SAFE_METHODS, BasePermission
from rest_framework.request import Request
from rest_framework.views import APIView


class RolePermission(BasePermission):
    """Base para permissões por papel. Defina `allowed_roles` na subclasse."""

    allowed_roles: tuple[str, ...] = ()
    message = "Você não tem permissão para acessar este recurso."

    def has_permission(self, request: Request, view: APIView) -> bool:
        user = request.user
        if not user or not user.is_authenticated or not user.is_active:
            return False
        if user.is_superuser:
            return True
        return user.role in self.allowed_roles


class IsOwner(RolePermission):
    """Somente o proprietário da Sua Barbearia."""

    allowed_roles = ("OWNER",)


class IsBarber(RolePermission):
    """Somente barbeiros."""

    allowed_roles = ("BARBER",)


class IsClient(RolePermission):
    """Somente clientes."""

    allowed_roles = ("CLIENT",)


class IsOwnerOrBarber(RolePermission):
    allowed_roles = ("OWNER", "BARBER")


class IsAuthenticatedRole(RolePermission):
    allowed_roles = ("OWNER", "BARBER", "CLIENT")


class IsOwnerOrReadOnly(BasePermission):
    """Leitura para qualquer autenticado; escrita apenas para OWNER."""

    message = "Apenas o proprietário pode alterar este recurso."

    def has_permission(self, request: Request, view: APIView) -> bool:
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if request.method in SAFE_METHODS:
            return True
        return user.is_superuser or user.role == "OWNER"


class IsSelfOrOwner(BasePermission):
    """Permite acesso ao próprio recurso do usuário, ou ao OWNER."""

    message = "Você só pode acessar os seus próprios dados."

    def has_object_permission(self, request: Request, view: APIView, obj: Any) -> bool:
        user = request.user
        if user.is_superuser or user.role == "OWNER":
            return True
        owner_user = getattr(obj, "user", None)
        return owner_user is not None and owner_user.pk == user.pk
