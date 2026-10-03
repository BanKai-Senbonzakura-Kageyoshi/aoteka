import tempfile
from datetime import timedelta
from io import BytesIO
from unittest.mock import patch
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from .models import (
	AppointmentRequest,
	DoctorProfile,
	DoctorConversation,
	DoctorMessage,
	DoctorReview,
	Medicine,
	PatientDiscount,
	PatientProfile,
	PaymentCard,
	PharmacyOrder,
	PharmacyOrderItem,
	Pharmacy,
	ShoppingCartItem,
	UserProfile,
)
from .services import SPECIALTIES, ask_gemini, get_discounted_price, route_symptoms


class MedicalPlatformTests(TestCase):
	def setUp(self):
		# directory_cache живёт между тестами (pk аптеки совпадает) — сбрасываем
		cache.clear()
		self.patient_user = User.objects.create_user(username='patient', password='test-password')
		self.patient = PatientProfile.objects.get(user=self.patient_user)
		self.doctor_user = User.objects.create_user(username='doctor', password='test-password')
		self.doctor = DoctorProfile.objects.create(
			user=self.doctor_user,
			specialty='Кардиолог',
			status=DoctorProfile.Status.FREE,
		)

	def card_payload(self, pharmacy=None, **overrides):
		"""Валидные данные карты для POST на страницу оплаты."""
		payload = {
			'card_number': '4111 1111 1111 1111',
			'exp_month': '12',
			'exp_year': str(timezone.now().year + 1),
			'cardholder_name': 'TEST USER',
			'cvc': '123',
		}
		payload.update(overrides)
		return payload

	def test_pharmacy_detail_adds_selected_medicine_to_cart(self):
		pharmacy = Pharmacy.objects.create(name='Тестовая аптека', address='Тестовый адрес')
		medicine = Medicine.objects.create(
			name='Тестовые таблетки',
			dosage_form=Medicine.DosageForm.TABLET,
			price=Decimal('250.00'),
		)
		pharmacy.medicines.add(medicine)
		self.client.login(username='patient', password='test-password')

		response = self.client.get(reverse('pharmacy_detail', args=[pharmacy.pk]))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'В корзину')
		response = self.client.post(reverse('cart_add'), {
			'pharmacy_id': pharmacy.pk,
			'medicine_id': medicine.pk,
			'quantity': 2,
			'next': reverse('pharmacy_detail', args=[pharmacy.pk]),
		})

		self.assertRedirects(response, reverse('pharmacy_detail', args=[pharmacy.pk]))
		self.assertEqual(
			ShoppingCartItem.objects.get(user=self.patient_user, pharmacy=pharmacy, medicine=medicine).quantity,
			2,
		)

	def test_home_header_shows_cart_badge_only_when_cart_has_items(self):
		self.client.force_login(self.patient_user)
		response = self.client.get(reverse('home'))
		self.assertNotContains(response, 'class="cart-count"')

		pharmacy = Pharmacy.objects.create(name='Аптека счётчика', address='Улица, 8')
		medicines = [
			Medicine.objects.create(
				name=f'Лекарство для счётчика {index}',
				dosage_form=Medicine.DosageForm.TABLET,
				price=Decimal('100.00'),
			)
			for index in range(2)
		]
		pharmacy.medicines.add(*medicines)
		for medicine in medicines:
			ShoppingCartItem.objects.create(user=self.patient_user, pharmacy=pharmacy, medicine=medicine)

		response = self.client.get(reverse('home'))

		self.assertContains(response, 'class="cart-count"')
		self.assertContains(response, 'aria-label="Позиций в корзине: 2"')

	def test_cart_add_rejects_external_return_url(self):
		self.client.force_login(self.patient_user)
		pharmacy = Pharmacy.objects.create(name='Аптека безопасного возврата', address='Улица, 9')
		medicine = Medicine.objects.create(
			name='Лекарство безопасного возврата',
			dosage_form=Medicine.DosageForm.TABLET,
			price=Decimal('100.00'),
		)
		pharmacy.medicines.add(medicine)

		response = self.client.post(reverse('cart_add'), {
			'pharmacy_id': pharmacy.pk,
			'medicine_id': medicine.pk,
			'quantity': 1,
			'next': 'https://example.invalid/',
		})

		self.assertRedirects(response, reverse('cart'))

	def test_doctor_directory_filters_specialty_and_price(self):
		doctor_user = User.objects.create_user(username='multi-doctor', password='test-password')
		DoctorProfile.objects.create(
			user=doctor_user,
			specialty='Терапевт',
			specialties=['Терапевт', 'Невролог'],
			consultation_price=Decimal('1400.00'),
			status=DoctorProfile.Status.OFFLINE,
		)

		response = self.client.get(reverse('doctor_directory'), {
			'specialty': 'Невролог',
			'min_price': '1200',
			'max_price': '1500',
		})

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'multi-doctor')

	def test_doctor_detail_displays_reviews_and_patient_can_start_chat(self):
		DoctorReview.objects.create(doctor=self.doctor, patient=self.patient, rating=5, comment='Очень внимательный врач')
		response = self.client.get(reverse('doctor_detail', args=[self.doctor.pk]))
		self.assertContains(response, 'Очень внимательный врач')
		self.client.force_login(self.patient_user)
		response = self.client.get(reverse('doctor_detail', args=[self.doctor.pk]))
		self.assertContains(response, 'Поговорить с врачом')
		self.assertContains(response, 'После подтверждения врача откроется чат')

		response = self.client.post(reverse('start_doctor_conversation', args=[self.doctor.pk]))
		appointment_request = AppointmentRequest.objects.get(patient=self.patient, doctor=self.doctor)

		self.assertRedirects(response, reverse('appointment_detail', args=[appointment_request.pk]))
		self.assertEqual(appointment_request.status, AppointmentRequest.Status.PENDING)
		self.assertFalse(DoctorConversation.objects.filter(patient=self.patient, doctor=self.doctor).exists())

	def test_doctor_acceptance_starts_chat_before_ten_minute_deadline(self):
		appointment_request = AppointmentRequest.objects.create(patient=self.patient, doctor=self.doctor)
		self.client.force_login(self.patient_user)
		self.assertContains(
			self.client.get(reverse('appointment_detail', args=[appointment_request.pk])),
			'10:00',
		)
		self.client.force_login(self.doctor_user)

		response = self.client.post(reverse('appointment_detail', args=[appointment_request.pk]), {'action': 'accept'})

		appointment_request.refresh_from_db()
		conversation = DoctorConversation.objects.get(patient=self.patient, doctor=self.doctor)
		self.assertEqual(appointment_request.status, AppointmentRequest.Status.ACCEPTED)
		self.assertRedirects(response, reverse('conversation_detail', args=[conversation.pk]))

	def test_expired_appointment_request_cannot_be_accepted(self):
		appointment_request = AppointmentRequest.objects.create(
			patient=self.patient,
			doctor=self.doctor,
			expires_at=timezone.now() - timedelta(seconds=1),
		)
		self.client.force_login(self.doctor_user)

		response = self.client.post(reverse('appointment_detail', args=[appointment_request.pk]), {'action': 'accept'})

		appointment_request.refresh_from_db()
		self.assertRedirects(response, reverse('appointment_detail', args=[appointment_request.pk]))
		self.assertEqual(appointment_request.status, AppointmentRequest.Status.EXPIRED)
		self.assertFalse(DoctorConversation.objects.filter(patient=self.patient, doctor=self.doctor).exists())

	def test_doctor_sends_available_medicine_and_patient_adds_it_to_cart(self):
		pharmacy = Pharmacy.objects.create(name='Аптека рекомендаций', address='Улица, 6')
		medicine = Medicine.objects.create(
			name='Рекомендованный препарат',
			dosage_form=Medicine.DosageForm.CAPSULE,
			price=Decimal('180.00'),
		)
		pharmacy.medicines.add(medicine)
		conversation = DoctorConversation.objects.create(patient=self.patient, doctor=self.doctor)
		self.client.force_login(self.doctor_user)

		response = self.client.post(reverse('conversation_detail', args=[conversation.pk]), {
			'pharmacy': pharmacy.pk,
			'medicine': medicine.pk,
		})

		self.assertRedirects(response, reverse('conversation_detail', args=[conversation.pk]))
		recommendation = DoctorMessage.objects.get(conversation=conversation)
		self.assertEqual(recommendation.medicine, medicine)
		self.assertEqual(recommendation.pharmacy, pharmacy)

		self.client.force_login(self.patient_user)
		response = self.client.get(reverse('conversation_detail', args=[conversation.pk]))
		self.assertContains(response, 'Добавить в корзину')
		response = self.client.post(reverse('cart_add'), {
			'pharmacy_id': pharmacy.pk,
			'medicine_id': medicine.pk,
			'quantity': 1,
		})
		self.assertRedirects(response, reverse('cart'))
		self.assertTrue(ShoppingCartItem.objects.filter(user=self.patient_user, pharmacy=pharmacy, medicine=medicine).exists())

	def test_registration_creates_patient_profile_and_discount(self):
		user = User.objects.create_user(username='new-patient', password='test-password')

		self.assertEqual(UserProfile.objects.get(user=user).primary_role, UserProfile.PrimaryRole.PATIENT)
		self.assertEqual(PatientProfile.objects.get(user=user).discount.discount_percent,
						 PatientDiscount.objects.get(patient__user=user).discount_percent)
		self.assertGreaterEqual(user.patient_profile.discount.discount_percent, 15)
		self.assertLessEqual(user.patient_profile.discount.discount_percent, 50)

	@patch('myapp.services.ask_gemini')
	def test_critical_symptoms_get_priority_without_external_ai(self, ask_gemini):
		result = route_symptoms(self.patient, 'Мне трудно дышать и есть сильное кровотечение')

		self.patient.refresh_from_db()
		self.assertTrue(result['is_critical'])
		self.assertEqual(self.patient.priority, 100)
		self.assertEqual(self.patient.urgency, PatientProfile.Urgency.CRITICAL)
		ask_gemini.assert_not_called()

	@patch('myapp.services.ask_gemini')
	def test_symptoms_use_local_routing_without_consent(self, ask_gemini):
		result = route_symptoms(self.patient, 'Болит грудь и беспокоит сердце')

		self.assertEqual(result['specialty'], 'Кардиолог')
		self.assertEqual(list(result['doctors']), [self.doctor])
		ask_gemini.assert_not_called()

	@patch('myapp.services.ask_gemini', return_value={'specialty': 'Терапевт', 'error': ''})
	def test_transliterated_back_pain_uses_orthopedist_fallback(self, ask_gemini):
		result = route_symptoms(self.patient, 'bolit spina', consent_to_ai=True)

		self.assertEqual(result['specialty'], 'Ортопед')
		self.assertIn('локальным ключевым словам', result['message'])
		ask_gemini.assert_called_once()

	@patch('myapp.services.ask_gemini', return_value={'specialty': 'Терапевт', 'error': ''})
	def test_transliterated_arm_pain_uses_orthopedist_rule(self, ask_gemini):
		result = route_symptoms(self.patient, 'bolit ruka', consent_to_ai=True)

		self.assertEqual(result['specialty'], 'Ортопед')
		self.assertIn('локальным ключевым словам', result['message'])
		ask_gemini.assert_called_once()

	@patch('myapp.services.ask_gemini', return_value={'specialty': 'Дерматолог', 'error': ''})
	def test_gemini_result_is_used_only_with_consent(self, ask_gemini):
		result = route_symptoms(
			self.patient,
			'Нужна консультация',
			consent_to_ai=True,
		)

		self.assertEqual(result['specialty'], 'Дерматолог')
		ask_gemini.assert_called_once()

	@patch('myapp.services.genai.Client')
	@patch.dict('os.environ', {'GEMINI_API_KEY': 'test-key'})
	def test_gemini_uses_structured_specialty_enum(self, client_class):
		client = client_class.return_value
		client.models.generate_content.return_value.text = '{"specialty": "Ортопед"}'

		result = ask_gemini('болит спина')

		self.assertEqual(result, {'specialty': 'Ортопед', 'error': ''})
		config = client.models.generate_content.call_args.kwargs['config']
		self.assertEqual(client.models.generate_content.call_args.kwargs['model'], 'gemini-3.8-flash')
		self.assertEqual(config.thinking_config.thinking_level, 'LOW')
		self.assertEqual(client_class.call_args.kwargs['http_options'].timeout, 10000)
		self.assertEqual(
			config.response_schema['properties']['specialty']['enum'],
			SPECIALTIES,
		)

	@patch('myapp.services.genai.Client')
	@patch.dict('os.environ', {'GEMINI_API_KEY': 'test-key'})
	def test_gemini_rejects_prose_and_unlisted_specialties(self, client_class):
		client = client_class.return_value
		client.models.generate_content.return_value.text = (
			'{"specialty": "Ортопед, но возможно терапевт"}'
		)

		self.assertEqual(ask_gemini('болит спина'), {'specialty': '', 'error': 'invalid_response'})

	@patch('myapp.services.genai.Client', side_effect=ValueError('invalid API key'))
	@patch.dict('os.environ', {'GEMINI_API_KEY': 'invalid-test-key'})
	def test_invalid_gemini_configuration_falls_back_without_server_error(self, client):
		self.assertEqual(ask_gemini('Выбери специальность'), {'specialty': '', 'error': 'request_failed'})
		client.assert_called_once()

	@patch.dict('os.environ', {}, clear=True)
	def test_missing_gemini_key_is_reported(self):
		self.assertEqual(ask_gemini('Выбери специальность'), {'specialty': '', 'error': 'missing_key'})

	@patch('myapp.services.ask_gemini', return_value={'specialty': '', 'error': 'missing_key'})
	def test_missing_gemini_key_is_explained_in_symptom_result(self, ask_gemini):
		result = route_symptoms(self.patient, 'Нужна консультация', consent_to_ai=True)

		self.assertIn('GEMINI_API_KEY', result['message'])
		self.assertIn('терапевт', result['message'])

	def test_doctor_rating_is_recalculated_after_review(self):
		DoctorReview.objects.create(doctor=self.doctor, patient=self.patient, rating=5)
		other_user = User.objects.create_user(username='other-patient', password='test-password')
		DoctorReview.objects.create(
			doctor=self.doctor,
			patient=PatientProfile.objects.get(user=other_user),
			rating=3,
		)

		self.doctor.refresh_from_db()
		self.assertEqual(self.doctor.rating, 4)
		self.assertEqual(self.doctor.reviews_count, 2)

	def test_medicine_exposes_3d_render_configuration(self):
		medicine = Medicine.objects.create(
			name='Таблетки',
			dosage_form=Medicine.DosageForm.TABLET,
			price='10.00',
			primary_color='#117755',
			secondary_color='#EEEEEE',
		)

		render_config = medicine.get_3d_render_config()

		self.assertEqual(render_config['geometry']['type'], 'cylinder')
		self.assertEqual(render_config['material']['primaryColor'], '#117755')
		self.assertEqual(render_config['material']['secondaryColor'], '#EEEEEE')

	def test_cart_add_update_and_checkout_creates_pharmacist_order(self):
		self.client.force_login(self.patient_user)
		pharmacy = Pharmacy.objects.create(name='Аптека корзины', address='Улица, 4')
		medicine = Medicine.objects.create(
			name='Препарат в корзину',
			dosage_form=Medicine.DosageForm.TABLET,
			price='20.00',
		)
		pharmacy.medicines.add(medicine)

		for quantity in [2, 1]:
			self.client.post(reverse('cart_add'), {
				'pharmacy_id': pharmacy.pk,
				'medicine_id': medicine.pk,
				'quantity': quantity,
			})

		cart_item = ShoppingCartItem.objects.get(user=self.patient_user, pharmacy=pharmacy, medicine=medicine)
		self.assertEqual(cart_item.quantity, 3)
		self.client.post(reverse('cart_item_update', args=[cart_item.pk]), {'quantity': 4})

		response = self.client.post(reverse('cart_checkout', args=[pharmacy.pk]), self.card_payload(pharmacy))
		order = PharmacyOrder.objects.get(user=self.patient_user, pharmacy=pharmacy)
		order_item = PharmacyOrderItem.objects.get(order=order)

		self.assertEqual(response.status_code, 200)
		self.assertTemplateUsed(response, 'myapp/payment_success.html')
		self.assertContains(response, 'Заказ оплачен')
		self.assertContains(response, f'#{order.pk}')
		self.assertEqual(order.status, PharmacyOrder.Status.PENDING)
		self.assertEqual(order.payment_method, PharmacyOrder.PaymentMethod.CARD)
		self.assertEqual(order.payment_status, PharmacyOrder.PaymentStatus.PAID)
		self.assertEqual(order_item.quantity, 4)
		self.assertEqual(order.total_amount, order_item.unit_price * 4)
		self.assertFalse(ShoppingCartItem.objects.filter(user=self.patient_user, pharmacy=pharmacy).exists())

	def test_checkout_never_stores_card_number_or_cvc(self):
		self.client.force_login(self.patient_user)
		pharmacy = Pharmacy.objects.create(name='Аптека оплаты', address='Улица, 8')
		medicine = Medicine.objects.create(
			name='Препарат по карте',
			dosage_form=Medicine.DosageForm.TABLET,
			price=Decimal('120.00'),
		)
		pharmacy.medicines.add(medicine)
		ShoppingCartItem.objects.create(
			user=self.patient_user, pharmacy=pharmacy, medicine=medicine, quantity=2,
		)
		card_number = '4111111111111111'
		discount, _ = PatientDiscount.objects.get_or_create(patient=self.patient)

		self.client.post(
			reverse('cart_checkout', args=[pharmacy.pk]),
			self.card_payload(card_number=card_number),
		)

		order = PharmacyOrder.objects.get(user=self.patient_user, pharmacy=pharmacy)
		expected = get_discounted_price(Decimal('120.00'), discount.discount_percent) * 2
		self.assertEqual(order.total_amount, expected)
		self.assertFalse(PaymentCard.objects.filter(card_number=card_number).exists())
		self.assertNotIn(card_number, repr(order.__dict__))

	def test_payment_page_renders_card_form(self):
		self.client.force_login(self.patient_user)
		pharmacy = Pharmacy.objects.create(name='Аптека оплаты', address='Улица, 6')
		medicine = Medicine.objects.create(
			name='Препарат',
			dosage_form=Medicine.DosageForm.TABLET,
			price=Decimal('90.00'),
		)
		pharmacy.medicines.add(medicine)
		ShoppingCartItem.objects.create(user=self.patient_user, pharmacy=pharmacy, medicine=medicine)

		response = self.client.get(reverse('cart_checkout', args=[pharmacy.pk]))

		self.assertEqual(response.status_code, 200)
		self.assertTemplateUsed(response, 'myapp/payment.html')
		self.assertContains(response, 'pay-card')
		self.assertContains(response, 'data-card-echo="number"')
		self.assertContains(response, 'name="cvc"')
		self.assertContains(response, 'name="card_number"')

	def test_checkout_with_invalid_card_renders_payment_page_and_keeps_cart(self):
		self.client.force_login(self.patient_user)
		pharmacy = Pharmacy.objects.create(name='Аптека без данных', address='Улица, 7')
		medicine = Medicine.objects.create(
			name='Дорогое лекарство',
			dosage_form=Medicine.DosageForm.TABLET,
			price=Decimal('500.00'),
		)
		pharmacy.medicines.add(medicine)
		ShoppingCartItem.objects.create(user=self.patient_user, pharmacy=pharmacy, medicine=medicine)

		response = self.client.post(reverse('cart_checkout', args=[pharmacy.pk]), {
			'card_number': '',
			'exp_month': '1',
			'exp_year': str(timezone.now().year - 1),
			'cardholder_name': '',
			'cvc': '',
		})

		self.assertEqual(response.status_code, 200)
		self.assertTemplateUsed(response, 'myapp/payment.html')
		self.assertContains(response, 'pay-form-errors')
		self.assertTrue(ShoppingCartItem.objects.filter(user=self.patient_user, pharmacy=pharmacy).exists())
		self.assertFalse(PharmacyOrder.objects.filter(user=self.patient_user, pharmacy=pharmacy).exists())

	def test_checkout_accepts_any_card_number_and_cvc(self):
		self.client.force_login(self.patient_user)
		pharmacy = Pharmacy.objects.create(name='Аптека любых цифр', address='Улица, 11')
		medicine = Medicine.objects.create(
			name='Лекарство',
			dosage_form=Medicine.DosageForm.TABLET,
			price=Decimal('50.00'),
		)
		pharmacy.medicines.add(medicine)
		ShoppingCartItem.objects.create(user=self.patient_user, pharmacy=pharmacy, medicine=medicine)

		response = self.client.post(reverse('cart_checkout', args=[pharmacy.pk]), self.card_payload(
			card_number='1234 5678',
			cvc='7',
		))

		self.assertEqual(response.status_code, 200)
		self.assertTemplateUsed(response, 'myapp/payment_success.html')
		self.assertTrue(PharmacyOrder.objects.filter(user=self.patient_user, pharmacy=pharmacy).exists())

	def test_checkout_with_expired_card_is_rejected(self):
		self.client.force_login(self.patient_user)
		pharmacy = Pharmacy.objects.create(name='Аптека со старой картой', address='Улица, 9')
		medicine = Medicine.objects.create(
			name='Лекарство',
			dosage_form=Medicine.DosageForm.TABLET,
			price=Decimal('50.00'),
		)
		pharmacy.medicines.add(medicine)
		ShoppingCartItem.objects.create(user=self.patient_user, pharmacy=pharmacy, medicine=medicine)

		now = timezone.now()
		response = self.client.post(reverse('cart_checkout', args=[pharmacy.pk]), self.card_payload(
			exp_month='1',
			exp_year=str(now.year if now.month > 1 else now.year - 1),
		))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'pay-form-errors')
		self.assertFalse(PharmacyOrder.objects.filter(user=self.patient_user, pharmacy=pharmacy).exists())
		self.assertTrue(ShoppingCartItem.objects.filter(user=self.patient_user, pharmacy=pharmacy).exists())

	def test_registration_creates_active_doctor_from_separate_form(self):
		response = self.client.post(reverse('register_doctor'), {
			'username': 'new-doctor',
			'password1': 'CarelineDoctor2026Strong',
			'password2': 'CarelineDoctor2026Strong',
			'first_name': 'Новый',
			'specialties': 'Кардиолог, Терапевт',
		})

		doctor = DoctorProfile.objects.get(user__username='new-doctor')
		self.assertRedirects(response, reverse('home'))
		self.assertEqual(doctor.status, DoctorProfile.Status.FREE)
		self.assertEqual(doctor.user.medical_profile.primary_role, UserProfile.PrimaryRole.DOCTOR)
		self.assertEqual(doctor.user.first_name, 'Новый')

	def test_registration_pages_are_distinct_by_role(self):
		patient_response = self.client.get(reverse('register_patient'))
		doctor_response = self.client.get(reverse('register_doctor'))

		self.assertContains(patient_response, 'Регистрация пациента')
		self.assertNotContains(patient_response, 'Специальности врача')
		self.assertContains(doctor_response, 'Регистрация врача')
		self.assertContains(doctor_response, 'Специальности врача')

	def test_doctor_becomes_available_after_login_but_busy_state_is_preserved(self):
		self.doctor.status = DoctorProfile.Status.OFFLINE
		self.doctor.save(update_fields=['status'])

		response = self.client.post(reverse('login'), {'username': 'doctor', 'password': 'test-password'})

		self.assertRedirects(response, reverse('home'))
		self.doctor.refresh_from_db()
		self.assertEqual(self.doctor.status, DoctorProfile.Status.FREE)

		self.doctor.status = DoctorProfile.Status.BUSY
		self.doctor.save(update_fields=['status'])
		self.client.post(reverse('logout'))
		self.client.post(reverse('login'), {'username': 'doctor', 'password': 'test-password'})
		self.doctor.refresh_from_db()
		self.assertEqual(self.doctor.status, DoctorProfile.Status.BUSY)

	def test_opening_doctor_portal_activates_existing_offline_session(self):
		self.doctor.status = DoctorProfile.Status.OFFLINE
		self.doctor.save(update_fields=['status'])
		self.doctor_user.medical_profile.primary_role = UserProfile.PrimaryRole.DOCTOR
		self.doctor_user.medical_profile.save(update_fields=['primary_role'])
		self.client.force_login(self.doctor_user)

		response = self.client.get(reverse('doctor_portal'))

		self.assertEqual(response.status_code, 200)
		self.doctor.refresh_from_db()
		self.assertEqual(self.doctor.status, DoctorProfile.Status.FREE)

	def test_uploaded_medicine_photo_is_saved_and_served(self):
		self.client.force_login(self.patient_user)
		pharmacy = Pharmacy.objects.create(name='Аптека фото', address='Улица, 5')
		image_buffer = BytesIO()
		Image.new('RGB', (2, 2), color='green').save(image_buffer, format='PNG')

		with tempfile.TemporaryDirectory() as media_directory:
			with override_settings(MEDIA_ROOT=media_directory):
				medicine = Medicine.objects.create(
					name='Препарат с фото',
					dosage_form=Medicine.DosageForm.CAPSULE,
					price='15.00',
					image=SimpleUploadedFile('medicine.png', image_buffer.getvalue(), content_type='image/png'),
				)
				pharmacy.medicines.add(medicine)
				response = self.client.get(reverse('pharmacy_detail', args=[pharmacy.pk]))

				self.assertEqual(response.status_code, 200)
				self.assertContains(response, medicine.image.url)
				self.assertTrue(medicine.image.storage.exists(medicine.image.name))

	def test_wallet_is_removed_and_demo_cards_have_unique_16_digit_numbers(self):
		self.assertFalse(hasattr(self.patient_user, 'wallet'))
		self.assertFalse(hasattr(self.patient_user, 'wallet_transactions'))

		card_numbers = set()
		for index in range(5):
			card = PaymentCard.objects.create(user=self.patient_user, cardholder_name=f'Patient {index + 1}')
			self.assertRegex(card.card_number, r'^\d{16}$')
			self.assertNotIn(card.card_number, card_numbers)
			card_numbers.add(card.card_number)

		self.assertEqual(len(card_numbers), 5)
		self.assertEqual(PaymentCard.objects.filter(user=self.patient_user).count(), 5)
		with self.assertRaises(ValueError):
			PaymentCard.objects.create(user=self.patient_user, cardholder_name='Too many cards')

	def test_profile_page_shows_orders_and_hides_card_creation(self):
		self.client.force_login(self.patient_user)
		pharmacy = Pharmacy.objects.create(name='Аптека истории', address='Улица, 3')
		PharmacyOrder.objects.create(
			user=self.patient_user,
			pharmacy=pharmacy,
			total_amount=Decimal('245.60'),
			payment_method=PharmacyOrder.PaymentMethod.CARD,
			payment_status=PharmacyOrder.PaymentStatus.PAID,
		)

		response = self.client.get(reverse('profile'))
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'ИСТОРИЯ ЗАКАЗОВ')
		self.assertContains(response, '245,60')
		self.assertNotContains(response, 'Пополнить баланс')
		self.assertNotContains(response, 'Добавить карту')
		self.assertNotContains(response, 'card-add-form')

		response = self.client.post(reverse('profile'), {'action': 'add_card'})
		self.assertEqual(response.status_code, 200)
		self.assertEqual(PaymentCard.objects.filter(user=self.patient_user).count(), 0)

	def test_homepage_and_login_render(self):
		response = self.client.get(reverse('home'))
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, reverse('doctor_detail', args=[self.doctor.pk]))
		self.assertContains(response, 'Профиль и запись')
		self.assertEqual(self.client.get(reverse('login')).status_code, 200)
