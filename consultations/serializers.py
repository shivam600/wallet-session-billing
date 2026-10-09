from rest_framework import serializers

from .models import ConsultationSession


class SessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConsultationSession
        fields = (
            "id",
            "user",
            "provider",
            "status",
            "per_minute_cost",
            "started_at",
            "ended_at",
            "billed_minutes",
            "billed_amount",
            "talk_seconds",
            "ended_by",
            "created_at",
        )
        read_only_fields = fields


class ProviderIdSerializer(serializers.Serializer):
    provider_id = serializers.IntegerField()


class CreateSessionSerializer(serializers.Serializer):
    provider_id = serializers.IntegerField(required=False)
