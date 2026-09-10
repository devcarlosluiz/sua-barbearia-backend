"""Sinais da app de avaliações: mantém a nota média do barbeiro atualizada."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.db.models import Avg, Count
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.barbers.models import Barber
from apps.reviews.models import Review


def recalculate_barber_rating(barber_id: int) -> None:
    aggregate = Review.objects.filter(barber_id=barber_id, is_published=True).aggregate(
        average=Avg("rating"), total=Count("id")
    )
    average = aggregate["average"] or Decimal("0.00")
    Barber.objects.filter(pk=barber_id).update(
        rating=Decimal(average).quantize(Decimal("0.01")),
        reviews_count=aggregate["total"] or 0,
    )


@receiver(post_save, sender=Review)
def update_rating_on_save(sender: type[Review], instance: Review, **kwargs: Any) -> None:
    recalculate_barber_rating(instance.barber_id)


@receiver(post_delete, sender=Review)
def update_rating_on_delete(sender: type[Review], instance: Review, **kwargs: Any) -> None:
    recalculate_barber_rating(instance.barber_id)
