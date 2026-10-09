from django.db.models import Q
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.models import User
from accounts.permissions import IsProviderRole, IsUserRole

from . import services
from .models import ConsultationSession
from .serializers import CreateSessionSerializer, ProviderIdSerializer, SessionSerializer


def _get_provider(provider_id):
    try:
        return User.objects.get(pk=provider_id, role=User.Role.PROVIDER)
    except User.DoesNotExist:
        raise ValidationError({"provider_id": "No provider with this id."})


class SessionViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = SessionSerializer

    def get_queryset(self):
        me = self.request.user
        return ConsultationSession.objects.filter(Q(user=me) | Q(provider=me)).order_by("-id")

    def get_permissions(self):
        if self.action in ("create", "assign", "start"):
            return [IsAuthenticated(), IsUserRole()]
        if self.action == "accept":
            return [IsAuthenticated(), IsProviderRole()]
        return [IsAuthenticated()]

    def _respond(self, session):
        return Response(SessionSerializer(session).data)

    def create(self, request):
        serializer = CreateSessionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        provider = None
        if "provider_id" in serializer.validated_data:
            provider = _get_provider(serializer.validated_data["provider_id"])
        session = services.create_session(request.user, provider)
        return Response(SessionSerializer(session).data, status=201)

    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        self.get_object()  # 404 if it isn't ours
        serializer = ProviderIdSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        provider = _get_provider(serializer.validated_data["provider_id"])
        return self._respond(services.assign_session(pk, request.user, provider))

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        self.get_object()
        return self._respond(services.accept_session(pk, request.user))

    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        self.get_object()
        return self._respond(services.start_session(pk, request.user))

    @action(detail=True, methods=["post"])
    def end(self, request, pk=None):
        self.get_object()
        return self._respond(services.end_session(pk, request.user))

    @action(detail=True, methods=["post"])
    def bill(self, request, pk=None):
        self.get_object()
        return self._respond(services.bill_session(pk, request.user))

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        self.get_object()
        return self._respond(services.cancel_session(pk, request.user))
