import random
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Avg, Count
from django.utils import timezone


def generate_discount():
	return random.randint(15, 50)


def generate_demo_card_number():
	while True:
		number = ''.join(str(random.randint(0, 9)) for _ in range(16))
		if not PaymentCard.objects.filter(card_number=number).exists():
			return number


def appointment_request_expiry():
	return timezone.now() + timedelta(minutes=10)


class UserProfile(models.Model):
	class PrimaryRole(models.TextChoices):
		PATIENT = 'PATIENT', 'Пациент'
		DOCTOR = 'DOCTOR', 'Врач'
		PHARMACIST = 'PHARMACIST', 'Фармацевт'
		ADMIN = 'ADMIN', 'Администратор'

	user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='medical_profile')
	primary_role = models.CharField(max_length=20, choices=PrimaryRole.choices, default=PrimaryRole.PATIENT)

	def __str__(self):
		return f'{self.user.username} — {self.get_primary_role_display()}'


class PatientProfile(models.Model):
	class Intent(models.TextChoices):
		IDLE = 'IDLE', 'Нет активного запроса'
		SEARCHING_MEDICINE = 'SEARCHING_MEDICINE', 'Поиск лекарства'
		SEEKING_DOCTOR = 'SEEKING_DOCTOR', 'Поиск врача'
		CALLING_AMBULANCE = 'CALLING_AMBULANCE', 'Обращение за экстренной помощью'
		UNDER_TREATMENT = 'UNDER_TREATMENT', 'На лечении'

	class Urgency(models.TextChoices):
		NORMAL = 'NORMAL', 'Обычная'
		URGENT = 'URGENT', 'Срочная'
		CRITICAL = 'CRITICAL', 'Критическая'

	user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='patient_profile')
	intent = models.CharField(max_length=30, choices=Intent.choices, default=Intent.IDLE)
	urgency = models.CharField(max_length=20, choices=Urgency.choices, default=Urgency.NORMAL)
	priority = models.PositiveSmallIntegerField(default=0, validators=[MaxValueValidator(100)])

	def __str__(self):
		return f'Пациент {self.user.username}'


class PatientDiscount(models.Model):
	patient = models.OneToOneField(PatientProfile, on_delete=models.CASCADE, related_name='discount')
	discount_percent = models.PositiveSmallIntegerField(
		default=generate_discount,
		validators=[MinValueValidator(15), MaxValueValidator(50)],
	)
	created_at = models.DateTimeField(auto_now_add=True)

	def __str__(self):
		return f'{self.patient.user.username}: {self.discount_percent}%'


class DoctorProfile(models.Model):
	class Status(models.TextChoices):
		FREE = 'FREE', 'Свободен'
		BUSY = 'BUSY', 'Занят'
		OFFLINE = 'OFFLINE', 'Не в сети'
		VACATION = 'VACATION', 'В отпуске'
		EMERGENCY = 'EMERGENCY', 'Экстренный вызов'

	user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='doctor_profile')
	specialty = models.CharField(max_length=100, blank=True, default='')
	specialties = models.JSONField(default=list, blank=True)
	consultation_price = models.DecimalField(max_digits=8, decimal_places=2, default=1200.00)
	status = models.CharField(max_length=20, choices=Status.choices, default=Status.OFFLINE)
	rating = models.DecimalField(max_digits=3, decimal_places=2, default=0)
	reviews_count = models.PositiveIntegerField(default=0)

	def __str__(self):
		return f'{self.get_full_name()} — {self.primary_specialty}'

	@property
	def primary_specialty(self):
		if self.specialties:
			return self.specialties[0]
		return self.specialty or 'Специальность не указана'

	@property
	def specialty_list(self):
		if self.specialties:
			return self.specialties
		if self.specialty:
			return [self.specialty]
		return []

	def get_full_name(self):
		return self.user.get_full_name() or self.user.username


class DoctorReview(models.Model):
	doctor = models.ForeignKey(DoctorProfile, on_delete=models.CASCADE, related_name='reviews')
	patient = models.ForeignKey(PatientProfile, on_delete=models.CASCADE, related_name='doctor_reviews')
	rating = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
	comment = models.TextField(blank=True)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ['-created_at']
		constraints = [
			models.UniqueConstraint(fields=['doctor', 'patient'], name='one_review_per_patient_doctor'),
		]

	def save(self, *args, **kwargs):
		super().save(*args, **kwargs)
		self.update_doctor_rating()

	def delete(self, *args, **kwargs):
		doctor = self.doctor
		result = super().delete(*args, **kwargs)
		self.update_doctor_rating(doctor)
		return result

	def update_doctor_rating(self, doctor=None):
		doctor = doctor or self.doctor
		review_summary = DoctorReview.objects.filter(doctor=doctor).aggregate(
			average_rating=Avg('rating'),
			reviews_count=Count('id'),
		)
		DoctorProfile.objects.filter(pk=doctor.pk).update(
			rating=review_summary['average_rating'] or 0,
			reviews_count=review_summary['reviews_count'],
		)

	def __str__(self):
		return f'{self.patient.user.username} → {self.doctor}: {self.rating}/5'


class Favorite(models.Model):
	"""Избранные врачи пользователя («звёздочка» на карточке)."""

	user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='favorites')
	doctor = models.ForeignKey(DoctorProfile, on_delete=models.CASCADE, related_name='favorited_by')
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ['-created_at']
		constraints = [
			models.UniqueConstraint(fields=['user', 'doctor'], name='one_favorite_per_user_doctor'),
		]

	def __str__(self):
		return f'{self.user.username} ♥ {self.doctor}'


class DoctorConversation(models.Model):
	patient = models.ForeignKey(PatientProfile, on_delete=models.CASCADE, related_name='doctor_conversations')
	doctor = models.ForeignKey(DoctorProfile, on_delete=models.CASCADE, related_name='conversations')
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ['-updated_at']
		constraints = [
			models.UniqueConstraint(fields=['patient', 'doctor'], name='one_conversation_per_patient_doctor'),
		]

	def __str__(self):
		return f'{self.patient.user.username} ↔ {self.doctor.get_full_name()}'


class AppointmentRequest(models.Model):
	class Status(models.TextChoices):
		PENDING = 'PENDING', 'Ожидает ответа'
		ACCEPTED = 'ACCEPTED', 'Принята'
		DECLINED = 'DECLINED', 'Отклонена'
		EXPIRED = 'EXPIRED', 'Время ответа истекло'

	patient = models.ForeignKey(PatientProfile, on_delete=models.CASCADE, related_name='appointment_requests')
	doctor = models.ForeignKey(DoctorProfile, on_delete=models.CASCADE, related_name='appointment_requests')
	status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
	created_at = models.DateTimeField(auto_now_add=True)
	expires_at = models.DateTimeField(default=appointment_request_expiry)
	accepted_at = models.DateTimeField(blank=True, null=True)

	class Meta:
		ordering = ['-created_at']

	def __str__(self):
		return f'{self.patient.user.username} → {self.doctor.get_full_name()}: {self.get_status_display()}'


class DoctorMessage(models.Model):
	conversation = models.ForeignKey(DoctorConversation, on_delete=models.CASCADE, related_name='messages')
	sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name='doctor_messages')
	body = models.TextField(blank=True)
	medicine = models.ForeignKey('Medicine', on_delete=models.PROTECT, blank=True, null=True, related_name='doctor_recommendations')
	pharmacy = models.ForeignKey('Pharmacy', on_delete=models.PROTECT, blank=True, null=True, related_name='doctor_recommendations')
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ['created_at']
		constraints = [
			models.CheckConstraint(
				condition=(
					models.Q(medicine__isnull=True, pharmacy__isnull=True)
					| models.Q(medicine__isnull=False, pharmacy__isnull=False)
				),
				name='doctor_message_medicine_pharmacy_pair',
			),
		]

	def __str__(self):
		return self.body[:80] or f'Рекомендация: {self.medicine}'


class Pharmacy(models.Model):
	name = models.CharField(max_length=150)
	address = models.CharField(max_length=255)
	is_open = models.BooleanField(default=True)
	description = models.TextField(blank=True, default='')
	rating = models.DecimalField(max_digits=3, decimal_places=2, default=4.80)
	review_count = models.PositiveIntegerField(default=0)
	medicines = models.ManyToManyField('Medicine', related_name='pharmacies', blank=True)

	def __str__(self):
		return self.name


class PharmacistProfile(models.Model):
	class ShiftStatus(models.TextChoices):
		ON_DUTY = 'ON_DUTY', 'На смене'
		OFF_DUTY = 'OFF_DUTY', 'Не на смене'

	user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='pharmacist_profile')
	pharmacy = models.ForeignKey(Pharmacy, on_delete=models.PROTECT, related_name='pharmacists')
	shift_status = models.CharField(max_length=20, choices=ShiftStatus.choices, default=ShiftStatus.OFF_DUTY)

	@property
	def active_orders(self):
		return MedicineOrder.objects.filter(
			pharmacist=self,
			status__in=[MedicineOrder.Status.PENDING, MedicineOrder.Status.PROCESSING],
		).count()

	def __str__(self):
		return f'{self.user.username} — {self.pharmacy.name}'


class MedicineCategory(models.Model):
	name = models.CharField(max_length=100, unique=True, verbose_name='Категория')
	slug = models.SlugField(unique=True)

	def __str__(self):
		return self.name


class Medicine(models.Model):
	class DosageForm(models.TextChoices):
		TABLET = 'TABLET', 'Таблетки'
		CAPSULE = 'CAPSULE', 'Капсулы'
		SYRUP = 'SYRUP', 'Сироп'
		SUPPOSITORY = 'SUPPOSITORY', 'Суппозитории'
		AMPOULE = 'AMPOULE', 'Ампулы'
		OINTMENT = 'OINTMENT', 'Мазь'

	name = models.CharField(max_length=150)
	active_ingredient = models.CharField(max_length=150, blank=True)
	image = models.ImageField(upload_to='medicines/', blank=True, null=True)
	categories = models.ManyToManyField(MedicineCategory, related_name='medicines', blank=True)
	dosage_form = models.CharField(max_length=20, choices=DosageForm.choices)
	price = models.DecimalField(max_digits=10, decimal_places=2)
	primary_color = models.CharField(max_length=7, default='#2E8B72')
	secondary_color = models.CharField(max_length=7, default='#E4F2EC')
	is_available = models.BooleanField(default=True)

	def get_3d_render_config(self):
		geometry = {
			self.DosageForm.TABLET: {'type': 'cylinder', 'radius': 0.45, 'height': 0.16},
			self.DosageForm.CAPSULE: {'type': 'capsule', 'radius': 0.28, 'height': 1.1},
			self.DosageForm.SYRUP: {'type': 'bottle', 'radius': 0.38, 'height': 1.2},
			self.DosageForm.SUPPOSITORY: {'type': 'cone', 'radius': 0.3, 'height': 1.0},
			self.DosageForm.AMPOULE: {'type': 'cylinder', 'radius': 0.18, 'height': 1.3},
			self.DosageForm.OINTMENT: {'type': 'tube', 'radius': 0.32, 'height': 1.1},
		}
		return {
			'geometry': geometry[self.dosage_form],
			'material': {
				'primaryColor': self.primary_color,
				'secondaryColor': self.secondary_color,
			},
		}

	def __str__(self):
		return self.name


class MedicalPrescription(models.Model):
	class Status(models.TextChoices):
		ACTIVE = 'ACTIVE', 'Действителен'
		COMPLETED = 'COMPLETED', 'Выполнен'
		CANCELLED = 'CANCELLED', 'Отменён'

	patient = models.ForeignKey(PatientProfile, on_delete=models.CASCADE, related_name='prescriptions')
	doctor = models.ForeignKey(DoctorProfile, on_delete=models.PROTECT, related_name='prescriptions')
	pharmacy = models.ForeignKey(Pharmacy, on_delete=models.PROTECT, related_name='prescriptions')
	status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
	instructions = models.TextField(blank=True)
	created_at = models.DateTimeField(auto_now_add=True)

	def __str__(self):
		return f'Рецепт #{self.pk} — {self.patient.user.username}'


class PrescriptionItem(models.Model):
	prescription = models.ForeignKey(MedicalPrescription, on_delete=models.CASCADE, related_name='items')
	medicine = models.ForeignKey(Medicine, on_delete=models.PROTECT, related_name='prescription_items')
	quantity = models.PositiveSmallIntegerField(default=1)
	dosage_instructions = models.CharField(max_length=255, blank=True)

	def __str__(self):
		return f'{self.medicine.name} × {self.quantity}'


class MedicineOrder(models.Model):
	class Status(models.TextChoices):
		PENDING = 'PENDING', 'Ожидает'
		PROCESSING = 'PROCESSING', 'В обработке'
		READY = 'READY', 'Готов к выдаче'
		COMPLETED = 'COMPLETED', 'Выдан'
		CANCELLED = 'CANCELLED', 'Отменён'

	prescription = models.ForeignKey(MedicalPrescription, on_delete=models.PROTECT, related_name='orders')
	pharmacist = models.ForeignKey(
		PharmacistProfile,
		on_delete=models.SET_NULL,
		related_name='orders',
		blank=True,
		null=True,
	)
	status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
	created_at = models.DateTimeField(auto_now_add=True)

	def __str__(self):
		return f'Заказ #{self.pk} — {self.get_status_display()}'


class ShoppingCartItem(models.Model):
	user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='shopping_cart_items')
	pharmacy = models.ForeignKey(Pharmacy, on_delete=models.CASCADE, related_name='shopping_cart_items')
	medicine = models.ForeignKey(Medicine, on_delete=models.CASCADE, related_name='shopping_cart_items')
	quantity = models.PositiveSmallIntegerField(
		default=1,
		validators=[MinValueValidator(1), MaxValueValidator(99)],
	)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		constraints = [
			models.UniqueConstraint(
				fields=['user', 'pharmacy', 'medicine'],
				name='one_cart_item_per_user_pharmacy_medicine',
			),
		]

	def __str__(self):
		return f'{self.user.username}: {self.medicine.name} × {self.quantity}'


class PharmacyOrder(models.Model):
	class PaymentMethod(models.TextChoices):
		CARD = 'CARD', 'Картой'
		BALANCE = 'BALANCE', 'Баланс Careline (ранее)'
		LEGACY = 'LEGACY', 'Старый заказ — требуется оплата'

	class PaymentStatus(models.TextChoices):
		PAID = 'PAID', 'Оплачено'
		UNPAID = 'UNPAID', 'Не оплачен'

	class Status(models.TextChoices):
		PENDING = 'PENDING', 'Ожидает проверки'
		PROCESSING = 'PROCESSING', 'В обработке'
		READY = 'READY', 'Готов к выдаче'
		COMPLETED = 'COMPLETED', 'Выдан'
		CANCELLED = 'CANCELLED', 'Отменён'

	user = models.ForeignKey(User, on_delete=models.PROTECT, related_name='pharmacy_orders')
	pharmacy = models.ForeignKey(Pharmacy, on_delete=models.PROTECT, related_name='customer_orders')
	discount_percent = models.PositiveSmallIntegerField(default=0)
	total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
	payment_method = models.CharField(max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.CARD)
	payment_status = models.CharField(max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.PAID)
	status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
	created_at = models.DateTimeField(auto_now_add=True)

	def __str__(self):
		return f'Покупка #{self.pk} — {self.user.username}'


class PharmacyOrderItem(models.Model):
	order = models.ForeignKey(PharmacyOrder, on_delete=models.CASCADE, related_name='items')
	medicine = models.ForeignKey(Medicine, on_delete=models.PROTECT, related_name='pharmacy_order_items')
	quantity = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(99)])
	unit_price = models.DecimalField(max_digits=10, decimal_places=2)

	def __str__(self):
		return f'{self.medicine.name} × {self.quantity}'


class PaymentCard(models.Model):
	user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='payment_cards')
	cardholder_name = models.CharField(max_length=100, blank=True, default='')
	card_number = models.CharField(max_length=16, unique=True, db_index=True)
	created_at = models.DateTimeField(auto_now_add=True)
	is_active = models.BooleanField(default=True)

	class Meta:
		ordering = ['-created_at']
		constraints = [
			models.UniqueConstraint(fields=['user', 'card_number'], name='unique_user_card_number'),
		]

	def save(self, *args, **kwargs):
		if not self.card_number:
			self.card_number = generate_demo_card_number()
		if not self.pk and PaymentCard.objects.filter(user=self.user).count() >= 5:
			raise ValueError('Нельзя сохранить более 5 карт на пользователя.')
		super().save(*args, **kwargs)

	def __str__(self):
		return f'{self.cardholder_name or self.user.username}: {self.card_number}'

# Create your models here.
