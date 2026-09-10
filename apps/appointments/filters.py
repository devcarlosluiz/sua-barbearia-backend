"""Filtros de agendamentos."""

from __future__ import annotations

import django_filters as filters

from apps.appointments.models import Appointment, AppointmentStatus


class AppointmentFilter(filters.FilterSet):
    status = filters.MultipleChoiceFilter(choices=AppointmentStatus.choices)
    date = filters.DateFilter(field_name="date")
    date_from = filters.DateFilter(field_name="date", lookup_expr="gte")
    date_to = filters.DateFilter(field_name="date", lookup_expr="lte")
    branch = filters.NumberFilter(field_name="branch_id")
    barber = filters.NumberFilter(field_name="barber_id")
    client = filters.NumberFilter(field_name="client_id")
    service = filters.NumberFilter(field_name="service_id")
    upcoming = filters.BooleanFilter(method="filter_upcoming")

    class Meta:
        model = Appointment
        fields = ["status", "date", "branch", "barber", "client", "service"]

    def filter_upcoming(self, queryset, name: str, value: bool):
        from django.utils import timezone

        from apps.appointments.models import BLOCKING_STATUSES

        today = timezone.localdate()
        if value:
            return queryset.filter(date__gte=today, status__in=BLOCKING_STATUSES)
        return queryset.filter(date__lt=today)
