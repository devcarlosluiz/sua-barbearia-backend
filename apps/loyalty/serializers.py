"""Serializers do programa de fidelidade."""

from __future__ import annotations

from rest_framework import serializers

from apps.loyalty.models import LoyaltyAccount, LoyaltyReward, LoyaltyTransaction


class LoyaltyTransactionSerializer(serializers.ModelSerializer):
    type_display = serializers.CharField(source="get_type_display", read_only=True)
    reward_name = serializers.CharField(source="reward.name", read_only=True, default=None)

    class Meta:
        model = LoyaltyTransaction
        fields = (
            "id",
            "uuid",
            "type",
            "type_display",
            "points",
            "balance_after",
            "description",
            "appointment",
            "sale",
            "reward",
            "reward_name",
            "created_at",
        )
        read_only_fields = fields


class LoyaltyAccountSerializer(serializers.ModelSerializer):
    client_name = serializers.CharField(source="client.user.full_name", read_only=True)
    points_value = serializers.SerializerMethodField()

    class Meta:
        model = LoyaltyAccount
        fields = (
            "id",
            "uuid",
            "client",
            "client_name",
            "balance",
            "lifetime_earned",
            "lifetime_redeemed",
            "points_value",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_points_value(self, obj: LoyaltyAccount) -> str:
        """Valor estimado do saldo em reais, usando a filial preferida."""
        branch = obj.client.preferred_branch
        if branch is None:
            from apps.branches.models import Branch

            branch = Branch.objects.filter(is_active=True).first()
        rate = branch.loyalty_point_value if branch else 0
        return f"{obj.balance * rate:.2f}"


class LoyaltyRewardSerializer(serializers.ModelSerializer):
    type_display = serializers.CharField(source="get_type_display", read_only=True)
    service_name = serializers.CharField(source="service.name", read_only=True, default=None)

    class Meta:
        model = LoyaltyReward
        fields = (
            "id",
            "uuid",
            "name",
            "description",
            "type",
            "type_display",
            "points_cost",
            "discount_value",
            "service",
            "service_name",
            "valid_until",
            "is_active",
            "created_at",
        )
        read_only_fields = ("id", "uuid", "created_at")

    def validate(self, attrs: dict) -> dict:
        from apps.loyalty.models import LoyaltyRewardType

        reward_type = attrs.get("type") or getattr(self.instance, "type", None)
        if reward_type == LoyaltyRewardType.FREE_SERVICE and not (
            attrs.get("service") or getattr(self.instance, "service", None)
        ):
            raise serializers.ValidationError(
                {"service": "Informe o serviço da recompensa gratuita."}
            )
        return attrs


class LoyaltyRedeemSerializer(serializers.Serializer):
    reward_id = serializers.IntegerField()
    client_id = serializers.IntegerField(
        required=False, help_text="Somente OWNER/BARBER podem resgatar por outro cliente."
    )


class LoyaltyAdjustSerializer(serializers.Serializer):
    client_id = serializers.IntegerField()
    points = serializers.IntegerField()
    description = serializers.CharField(max_length=255)
