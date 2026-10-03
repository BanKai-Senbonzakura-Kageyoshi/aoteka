from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import PatientDiscount, PatientProfile, UserProfile


@receiver(post_save, sender=User)
def create_patient_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(user=instance)
        patient = PatientProfile.objects.create(user=instance)
        PatientDiscount.objects.create(patient=patient)