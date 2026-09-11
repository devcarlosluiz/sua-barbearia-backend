"""Endpoints de autenticação e gestão de usuários."""

from __future__ import annotations

import logging

from django.contrib.auth.models import update_last_login
from django.db import transaction
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status, viewsets
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from apps.accounts.models import PasswordResetToken, User
from apps.accounts.serializers import (
    ChangePasswordSerializer,
    ForgotPasswordSerializer,
    GoogleAuthSerializer,
    LogoutSerializer,
    RegisterSerializer,
    ResetPasswordSerializer,
    SuaBarbeariaTokenObtainPairSerializer,
    UserAdminSerializer,
    UserProfileUpdateSerializer,
    UserSerializer,
)
from apps.accounts.tasks import send_password_reset_email
from apps.core.mixins import AuditableViewSetMixin
from apps.core.models import AuditAction
from apps.core.permissions import IsOwner
from apps.core.services import audit
from apps.core.services.images import normalize_avatar

logger = logging.getLogger("suabarbearia.security")


@extend_schema(tags=["Autenticação"])
class LoginView(TokenObtainPairView):
    """Autentica por e-mail e senha, devolvendo access, refresh e o usuário."""

    permission_classes = [AllowAny]
    throttle_scope = "login"

    def post(self, request: Request, *args, **kwargs) -> Response:
        response = super().post(request, *args, **kwargs)
        email = request.data.get("email", "")
        if response.status_code == status.HTTP_200_OK:
            user_id = response.data.get("user", {}).get("id")
            audit.log_action(
                action=AuditAction.LOGIN,
                entity="accounts.User",
                entity_id=user_id or "",
                new_data={"email": email},
                user=User.objects.filter(pk=user_id).first(),
            )
        return response


@extend_schema(tags=["Autenticação"])
class RefreshView(TokenRefreshView):
    """Gera um novo access token a partir do refresh token."""

    permission_classes = [AllowAny]


@extend_schema(tags=["Autenticação"])
class LogoutView(GenericAPIView):
    """Encerra a sessão invalidando o refresh token."""

    serializer_class = LogoutSerializer
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={204: OpenApiResponse(description="Sessão encerrada")})
    def post(self, request: Request) -> Response:
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        audit.log_action(
            action=AuditAction.LOGOUT,
            entity="accounts.User",
            entity_id=request.user.pk,
            user=request.user,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["Autenticação"])
class RegisterView(GenericAPIView):
    """Cadastro público de cliente. Já devolve os tokens de acesso."""

    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]
    throttle_scope = "login"

    @extend_schema(responses={201: UserSerializer})
    @transaction.atomic
    def post(self, request: Request) -> Response:
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        refresh = SuaBarbeariaTokenObtainPairSerializer.get_token(user)
        audit.log_create(user, user=user)
        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "user": UserSerializer(user, context=self.get_serializer_context()).data,
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["Autenticação"])
class GoogleAuthView(GenericAPIView):
    """Entrar com o Google. Cadastra na primeira vez e faz login nas demais.

    Não pede senha nem data de nascimento: o app envia o `id_token` do Sign in
    with Google e recebe de volta o mesmo par de tokens do login normal.
    Responde 201 quando a conta acabou de ser criada e 200 quando já existia.
    """

    serializer_class = GoogleAuthSerializer
    permission_classes = [AllowAny]
    throttle_scope = "login"

    @extend_schema(responses={200: UserSerializer, 201: UserSerializer})
    def post(self, request: Request) -> Response:
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        created = serializer.created

        refresh = SuaBarbeariaTokenObtainPairSerializer.get_token(user)
        # O login por e-mail e senha atualiza `last_login` pelo SimpleJWT
        # (UPDATE_LAST_LOGIN); aqui o token é emitido direto, então cabe a nós.
        update_last_login(None, user)

        if created:
            audit.log_create(user, user=user)
        audit.log_action(
            action=AuditAction.LOGIN,
            entity="accounts.User",
            entity_id=user.pk,
            new_data={"email": user.email, "provider": "google"},
            user=user,
        )
        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "created": created,
                "user": UserSerializer(user, context=self.get_serializer_context()).data,
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


@extend_schema(tags=["Autenticação"])
class MeView(APIView):
    """Dados do usuário autenticado, incluindo o perfil correspondente ao papel."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: UserSerializer})
    def get(self, request: Request) -> Response:
        return Response(self._build_payload(request))

    @extend_schema(request=UserProfileUpdateSerializer, responses={200: UserSerializer})
    def patch(self, request: Request) -> Response:
        serializer = UserProfileUpdateSerializer(
            request.user, data=request.data, partial=True, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        old_data = audit.serialize_instance(request.user)

        foto = serializer.validated_data.get("avatar")
        if foto is not None:
            # Trocar a foto não pode deixar o arquivo anterior ocupando disco.
            if request.user.avatar:
                request.user.avatar.delete(save=False)
            serializer.validated_data["avatar"] = normalize_avatar(foto)

        serializer.save()
        audit.log_update(request.user, old_data, user=request.user)
        return Response(self._build_payload(request))

    def _build_payload(self, request: Request) -> dict:
        user = request.user
        payload = {"user": UserSerializer(user, context={"request": request}).data}

        if user.is_barber:
            from apps.barbers.serializers import BarberSerializer

            barber = getattr(user, "barber_profile", None)
            payload["barber"] = (
                BarberSerializer(barber, context={"request": request}).data if barber else None
            )
        elif user.is_client:
            from apps.clients.serializers import ClientSerializer

            client = getattr(user, "client_profile", None)
            payload["client"] = (
                ClientSerializer(client, context={"request": request}).data if client else None
            )
        return payload


@extend_schema(
    tags=["Autenticação"],
    summary="Remover a foto de perfil",
    responses={200: UserSerializer},
)
class MeAvatarView(APIView):
    """Volta para o avatar de iniciais.

    Rota própria porque `DELETE /auth/me/` significaria excluir a conta.
    """

    permission_classes = [IsAuthenticated]

    def delete(self, request: Request) -> Response:
        user = request.user
        if user.avatar:
            user.avatar.delete(save=False)
            user.avatar = None
            user.save(update_fields=["avatar", "updated_at"])
        return Response(UserSerializer(user, context={"request": request}).data)


@extend_schema(tags=["Autenticação"])
class ChangePasswordView(GenericAPIView):
    serializer_class = ChangePasswordSerializer
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={204: OpenApiResponse(description="Senha alterada")})
    def post(self, request: Request) -> Response:
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        logger.info("Senha alterada para o usuário %s", request.user.pk)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["Autenticação"])
class ForgotPasswordView(GenericAPIView):
    """Envia o link de redefinição. Responde sempre 202 (não revela cadastro)."""

    serializer_class = ForgotPasswordSerializer
    permission_classes = [AllowAny]
    throttle_scope = "password_reset"

    @extend_schema(responses={202: OpenApiResponse(description="Solicitação registrada")})
    def post(self, request: Request) -> Response:
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]

        user = User.objects.filter(email=email, is_active=True).first()
        if user is not None:
            reset = PasswordResetToken.issue(user)
            send_password_reset_email.delay(user.pk, reset.token)
        else:
            logger.info("Solicitação de reset para e-mail inexistente.")

        return Response(
            {
                "success": True,
                "data": None,
                "message": (
                    "Se houver uma conta com este e-mail, enviaremos as instruções "
                    "de redefinição em instantes."
                ),
                "errors": None,
            },
            status=status.HTTP_202_ACCEPTED,
        )


@extend_schema(tags=["Autenticação"])
class ResetPasswordView(GenericAPIView):
    serializer_class = ResetPasswordSerializer
    permission_classes = [AllowAny]
    throttle_scope = "password_reset"

    @extend_schema(responses={204: OpenApiResponse(description="Senha redefinida")})
    def post(self, request: Request) -> Response:
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        logger.info("Senha redefinida via token para o usuário %s", user.pk)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["Usuários"])
class UserViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """Gestão de usuários — exclusivo do OWNER."""

    serializer_class = UserAdminSerializer
    permission_classes = [IsOwner]
    queryset = User.objects.all()
    search_fields = ("first_name", "last_name", "email", "phone")
    filterset_fields = ("role", "is_active", "is_verified")
    ordering_fields = ("first_name", "created_at")
    ordering = ("first_name",)
