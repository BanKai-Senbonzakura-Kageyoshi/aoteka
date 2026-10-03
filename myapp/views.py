from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView, LogoutView
from django.db import models, transaction
from django.db.models import Count, Q
from decimal import Decimal
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.decorators.cache import cache_page
from django.views.generic import CreateView

from .forms import CardPaymentForm, CartAddForm, CartQuantityForm, DoctorMessageForm, DoctorReviewForm, RegisterForm, SymptomForm
from .models import (
	AppointmentRequest,
	DoctorConversation,
	DoctorMessage,
	DoctorProfile,
	DoctorReview,
	Favorite,
	MedicineOrder,
	MedicalPrescription,
	Medicine,
	PatientProfile,
	PatientDiscount,
	Pharmacy,
	PharmacyOrder,
	PharmacyOrderItem,
	ShoppingCartItem,
	UserProfile,
)
from .services import get_discounted_price, route_symptoms


def directory_cache(view_func):
	"""Кэш справочников только в проде (DEBUG=False) — в разработке и
	тестах страница всегда свежая, в тестах кэш вообще не активируется."""
	if settings.DEBUG:
		return view_func
	return cache_page(60 * 5)(view_func)


class HomeView(View):
	def get(self, request):
		doctors = DoctorProfile.objects.filter(status=DoctorProfile.Status.FREE).order_by('-rating')[:6]
		context = {'doctors': doctors}

		if request.user.is_authenticated:
			patient, created = PatientProfile.objects.get_or_create(user=request.user)
			context['patient'] = patient
			context['prescriptions'] = MedicalPrescription.objects.filter(
				patient=patient,
				status=MedicalPrescription.Status.ACTIVE,
			).select_related('doctor', 'pharmacy').order_by('-created_at')
			context['discount'] = getattr(patient, 'discount', None)

		return render(request, 'myapp/home.html', context)


class UserRegisterView(CreateView):
	form_class = RegisterForm
	template_name = 'myapp/register.html'
	success_url = reverse_lazy('home')

	def dispatch(self, request, *args, **kwargs):
		if request.method == 'GET':
			role = kwargs.get('role')
			if role == 'doctor':
				self.initial = {'primary_role': UserProfile.PrimaryRole.DOCTOR}
			elif role == 'patient':
				self.initial = {'primary_role': UserProfile.PrimaryRole.PATIENT}
		return super().dispatch(request, *args, **kwargs)

	def get_form_kwargs(self):
		kwargs = super().get_form_kwargs()
		kwargs['role'] = self.kwargs.get('role')
		return kwargs

	def form_valid(self, form):
		response = super().form_valid(form)
		user_profile = self.object.medical_profile
		primary_role = form.cleaned_data.get('registration_role') or form.cleaned_data.get('primary_role')
		self.object.first_name = form.cleaned_data.get('first_name', '')
		self.object.last_name = form.cleaned_data.get('last_name', '')
		self.object.save(update_fields=['first_name', 'last_name'])
		user_profile.primary_role = primary_role
		user_profile.save(update_fields=['primary_role'])

		if primary_role == UserProfile.PrimaryRole.DOCTOR:
			specialties = form.cleaned_data.get('specialty_choices') or [form.cleaned_data.get('specialty', '').strip()]
			specialties = [name.strip() for name in specialties if name and name.strip()]
			primary_specialty = specialties[0] if specialties else form.cleaned_data.get('specialty', '').strip()
			DoctorProfile.objects.create(
				user=self.object,
				specialty=primary_specialty,
				specialties=specialties,
				status=DoctorProfile.Status.FREE,
			)
			messages.success(self.request, 'Профиль активен: теперь пациенты могут записываться на приём.')

		login(self.request, self.object)
		return response


class PatientRegisterView(UserRegisterView):
	template_name = 'myapp/register_patient.html'


class DoctorRegisterView(UserRegisterView):
	template_name = 'myapp/register_doctor.html'


class UserLoginView(LoginView):
	template_name = 'myapp/login.html'
	redirect_authenticated_user = True

	def form_valid(self, form):
		response = super().form_valid(form)
		DoctorProfile.objects.filter(
			user=self.request.user,
			status=DoctorProfile.Status.OFFLINE,
		).update(status=DoctorProfile.Status.FREE)
		return response


class UserLogoutView(LogoutView):
	next_page = 'home'


class SymptomRouteView(LoginRequiredMixin, View):
	template_name = 'myapp/assistant.html'

	def get(self, request):
		return render(request, self.template_name, {'form': SymptomForm()})

	def post(self, request):
		form = SymptomForm(request.POST)
		if form.is_valid():
			patient, created = PatientProfile.objects.get_or_create(user=request.user)
			result = route_symptoms(
				patient,
				form.cleaned_data['description'],
				consent_to_ai=form.cleaned_data['consent_to_ai'],
			)
			return render(request, self.template_name, {'form': form, **result})

		return render(request, self.template_name, {'form': form})


class CartAddView(LoginRequiredMixin, View):
	def post(self, request):
		return_url = request.POST.get('next') or request.META.get('HTTP_REFERER', '')
		if not url_has_allowed_host_and_scheme(
			return_url,
			allowed_hosts={request.get_host()},
			require_https=request.is_secure(),
		):
			return_url = reverse('cart')

		form = CartAddForm(request.POST)
		if not form.is_valid():
			messages.error(request, 'Проверьте количество товара.')
			return redirect(return_url)

		pharmacy = get_object_or_404(
			Pharmacy,
			pk=form.cleaned_data['pharmacy_id'],
			is_open=True,
		)
		medicine = get_object_or_404(
			Medicine,
			pk=form.cleaned_data['medicine_id'],
			is_available=True,
			pharmacies=pharmacy,
		)
		cart_item, created = ShoppingCartItem.objects.get_or_create(
			user=request.user,
			pharmacy=pharmacy,
			medicine=medicine,
			defaults={'quantity': form.cleaned_data['quantity']},
		)
		if not created:
			cart_item.quantity = min(99, cart_item.quantity + form.cleaned_data['quantity'])
			cart_item.save(update_fields=['quantity'])

		messages.success(request, 'Лекарство добавлено в корзину.')
		return redirect(return_url)


class ShoppingCartView(LoginRequiredMixin, View):
	def get(self, request):
		patient, created = PatientProfile.objects.get_or_create(user=request.user)
		discount, created = PatientDiscount.objects.get_or_create(patient=patient)
		pharmacies = Pharmacy.objects.filter(
			shopping_cart_items__user=request.user,
		).distinct().order_by('name')
		cart_groups = []

		for pharmacy in pharmacies:
			items = list(
				ShoppingCartItem.objects.filter(user=request.user, pharmacy=pharmacy)
				.select_related('medicine', 'pharmacy')
				.order_by('medicine__name')
			)
			total = Decimal('0.00')
			for item in items:
				item.unit_price = get_discounted_price(item.medicine.price, discount.discount_percent)
				item.line_total = item.unit_price * item.quantity
				total += item.line_total
			cart_groups.append({
				'pharmacy': pharmacy,
				'items': items,
				'total': total,
			})

		return render(request, 'myapp/cart.html', {
			'cart_groups': cart_groups,
			'discount_percent': discount.discount_percent,
		})


class CartItemUpdateView(LoginRequiredMixin, View):
	def post(self, request, item_id):
		cart_item = get_object_or_404(ShoppingCartItem, pk=item_id, user=request.user)
		form = CartQuantityForm(request.POST)
		if form.is_valid():
			cart_item.quantity = form.cleaned_data['quantity']
			cart_item.save(update_fields=['quantity'])
		else:
			messages.error(request, 'Количество должно быть от 1 до 99.')
		return redirect('cart')


class CartItemRemoveView(LoginRequiredMixin, View):
	def post(self, request, item_id):
		cart_item = get_object_or_404(ShoppingCartItem, pk=item_id, user=request.user)
		cart_item.delete()
		messages.success(request, 'Лекарство удалено из корзины.')
		return redirect('cart')


class PharmacyCheckoutView(LoginRequiredMixin, View):
	def get(self, request, pharmacy_id):
		pharmacy = get_object_or_404(Pharmacy, pk=pharmacy_id, is_open=True)
		patient, created = PatientProfile.objects.get_or_create(user=request.user)
		discount, created = PatientDiscount.objects.get_or_create(patient=patient)
		items, total = self._cart_snapshot(request, pharmacy, discount)
		if not items:
			messages.error(request, 'Корзина этой аптеки пуста.')
			return redirect('cart')
		return render(request, 'myapp/payment.html', {
			'pharmacy': pharmacy,
			'items': items,
			'total': total,
			'discount_percent': discount.discount_percent,
			'payment_form': CardPaymentForm(),
		})

	@staticmethod
	def _cart_snapshot(request, pharmacy, discount):
		items = list(
			ShoppingCartItem.objects.filter(user=request.user, pharmacy=pharmacy)
			.select_related('medicine')
			.order_by('medicine__name')
		)
		total = Decimal('0.00')
		for item in items:
			item.unit_price = get_discounted_price(item.medicine.price, discount.discount_percent)
			item.line_total = item.unit_price * item.quantity
			total += item.line_total
		return items, total

	def post(self, request, pharmacy_id):
		pharmacy = get_object_or_404(Pharmacy, pk=pharmacy_id, is_open=True)
		patient, created = PatientProfile.objects.get_or_create(user=request.user)
		discount, created = PatientDiscount.objects.get_or_create(patient=patient)

		payment_form = CardPaymentForm(request.POST)
		if not payment_form.is_valid():
			items, total = self._cart_snapshot(request, pharmacy, discount)
			return render(request, 'myapp/payment.html', {
				'pharmacy': pharmacy,
				'items': items,
				'total': total,
				'discount_percent': discount.discount_percent,
				'payment_form': payment_form,
			})

		with transaction.atomic():
			cart_items = list(
				ShoppingCartItem.objects.select_for_update()
				.filter(user=request.user, pharmacy=pharmacy)
				.select_related('medicine')
			)
			if not cart_items:
				messages.error(request, 'Корзина этой аптеки пуста.')
				return redirect('cart')

			available_ids = set(pharmacy.medicines.filter(is_available=True).values_list('id', flat=True))
			if any(item.medicine_id not in available_ids for item in cart_items):
				messages.error(request, 'Некоторые лекарства больше недоступны. Обновите корзину.')
				return redirect('cart')

			order_items = []
			total = Decimal('0.00')
			for item in cart_items:
				unit_price = get_discounted_price(item.medicine.price, discount.discount_percent)
				total += unit_price * item.quantity
				order_items.append(PharmacyOrderItem(
					order=None,
					medicine=item.medicine,
					quantity=item.quantity,
					unit_price=unit_price,
				))

			order = PharmacyOrder.objects.create(
				user=request.user,
				pharmacy=pharmacy,
				discount_percent=discount.discount_percent,
				payment_method=PharmacyOrder.PaymentMethod.CARD,
				payment_status=PharmacyOrder.PaymentStatus.PAID,
			)
			for order_item in order_items:
				order_item.order = order
			PharmacyOrderItem.objects.bulk_create(order_items)
			order.total_amount = total
			order.save(update_fields=['total_amount'])
			ShoppingCartItem.objects.filter(user=request.user, pharmacy=pharmacy).delete()

		patient.intent = PatientProfile.Intent.SEARCHING_MEDICINE
		patient.save(update_fields=['intent'])
		return render(request, 'myapp/payment_success.html', {
			'order': order,
			'pharmacy': pharmacy,
			'total': total,
			'items': PharmacyOrderItem.objects.filter(order=order).select_related('medicine'),
		})


class MedicineOrderView(LoginRequiredMixin, View):
	def post(self, request, prescription_id):
		patient, created = PatientProfile.objects.get_or_create(user=request.user)
		prescription = get_object_or_404(
			MedicalPrescription,
			pk=prescription_id,
			patient=patient,
			status=MedicalPrescription.Status.ACTIVE,
		)
		existing_order = MedicineOrder.objects.filter(
			prescription=prescription,
			status__in=[MedicineOrder.Status.PENDING, MedicineOrder.Status.PROCESSING],
		).first()

		if existing_order:
			messages.info(request, 'Для этого рецепта уже есть активный заказ.')
		else:
			MedicineOrder.objects.create(prescription=prescription)
			patient.intent = PatientProfile.Intent.SEARCHING_MEDICINE
			patient.save()
			messages.success(request, 'Запрос передан аптеке для проверки фармацевтом.')

		return redirect('home')


class DoctorReviewView(LoginRequiredMixin, CreateView):
	form_class = DoctorReviewForm
	template_name = 'myapp/review_form.html'

	def get_success_url(self):
		return reverse_lazy('doctor_detail', kwargs={'doctor_id': self.kwargs['doctor_id']})

	def get_context_data(self, **kwargs):
		context = super().get_context_data(**kwargs)
		context['doctor'] = get_object_or_404(DoctorProfile, pk=self.kwargs['doctor_id'])
		return context

	def form_valid(self, form):
		doctor = get_object_or_404(DoctorProfile, pk=self.kwargs['doctor_id'])
		if doctor.user_id == self.request.user.pk:
			form.add_error(None, 'Нельзя оставить отзыв самому себе.')
			return self.form_invalid(form)
		patient, created = PatientProfile.objects.get_or_create(user=self.request.user)
		if DoctorReview.objects.filter(doctor=doctor, patient=patient).exists():
			form.add_error(None, 'Вы уже оставили отзыв этому врачу.')
			return self.form_invalid(form)

		form.instance.doctor = doctor
		form.instance.patient = patient
		return super().form_valid(form)


def user_is_doctor(user):
	profile = getattr(user, 'medical_profile', None)
	return bool(profile and profile.primary_role == UserProfile.PrimaryRole.DOCTOR)


def doctor_detail(request, doctor_id):
	doctor = get_object_or_404(DoctorProfile.objects.select_related('user'), pk=doctor_id)
	reviews = doctor.reviews.select_related('patient__user').order_by('-created_at')
	can_review = request.user.is_authenticated and doctor.user_id != request.user.pk
	can_book = request.user.is_authenticated and not user_is_doctor(request.user) and doctor.user_id != request.user.pk and doctor.status == DoctorProfile.Status.FREE
	return render(request, 'myapp/doctor_detail.html', {
		'doctor': doctor,
		'reviews': reviews,
		'can_review': can_review,
		'has_reviewed': request.user.is_authenticated and reviews.filter(patient__user=request.user).exists(),
		'can_message': request.user.is_authenticated and not user_is_doctor(request.user) and doctor.user_id != request.user.pk,
		'can_book': can_book,
		'is_favorite': request.user.is_authenticated and Favorite.objects.filter(user=request.user, doctor=doctor).exists(),
	})


def start_doctor_conversation(request, doctor_id):
	if not request.user.is_authenticated:
		return redirect('login')
	if request.method != 'POST':
		return redirect('doctor_detail', doctor_id=doctor_id)
	if user_is_doctor(request.user):
		return redirect('doctor_portal')
	doctor = get_object_or_404(DoctorProfile, pk=doctor_id)
	if doctor.user_id == request.user.pk:
		return redirect('doctor_detail', doctor_id=doctor.pk)
	if doctor.status != DoctorProfile.Status.FREE:
		messages.error(request, 'Сейчас врач не принимает новые запросы.')
		return redirect('doctor_detail', doctor_id=doctor.pk)
	patient, _ = PatientProfile.objects.get_or_create(user=request.user)
	now = timezone.now()
	AppointmentRequest.objects.filter(
		patient=patient,
		doctor=doctor,
		status=AppointmentRequest.Status.PENDING,
		expires_at__lte=now,
	).update(status=AppointmentRequest.Status.EXPIRED)
	pending_request = AppointmentRequest.objects.filter(
		patient=patient,
		doctor=doctor,
		status=AppointmentRequest.Status.PENDING,
	).first()
	if pending_request:
		return redirect('appointment_detail', request_id=pending_request.pk)
	appointment_request = AppointmentRequest.objects.create(patient=patient, doctor=doctor)
	return redirect('appointment_detail', request_id=appointment_request.pk)


def expire_pending_appointment_requests(queryset):
	queryset.filter(
		status=AppointmentRequest.Status.PENDING,
		expires_at__lte=timezone.now(),
	).update(status=AppointmentRequest.Status.EXPIRED)


def appointment_detail(request, request_id):
	if not request.user.is_authenticated:
		return redirect('login')
	appointment_request = get_object_or_404(
		AppointmentRequest.objects.select_related('patient__user', 'doctor__user'),
		pk=request_id,
	)
	is_doctor = appointment_request.doctor.user_id == request.user.pk
	is_patient = appointment_request.patient.user_id == request.user.pk
	if not (is_doctor or is_patient):
		return redirect('message_inbox')
	expire_pending_appointment_requests(AppointmentRequest.objects.filter(pk=appointment_request.pk))
	appointment_request.refresh_from_db()

	if request.method == 'POST' and is_doctor:
		with transaction.atomic():
			appointment_request = AppointmentRequest.objects.select_for_update().get(pk=request_id)
			if appointment_request.status != AppointmentRequest.Status.PENDING or appointment_request.expires_at <= timezone.now():
				appointment_request.status = AppointmentRequest.Status.EXPIRED
				appointment_request.save(update_fields=['status'])
				messages.error(request, 'Время ответа истекло. Запрос больше нельзя принять.')
			elif request.POST.get('action') == 'accept':
				appointment_request.status = AppointmentRequest.Status.ACCEPTED
				appointment_request.accepted_at = timezone.now()
				appointment_request.save(update_fields=['status', 'accepted_at'])
				conversation, _ = DoctorConversation.objects.get_or_create(
					patient=appointment_request.patient,
					doctor=appointment_request.doctor,
				)
				return redirect('conversation_detail', conversation_id=conversation.pk)
			elif request.POST.get('action') == 'decline':
				appointment_request.status = AppointmentRequest.Status.DECLINED
				appointment_request.save(update_fields=['status'])
				messages.info(request, 'Запрос отклонён.')
		return redirect('appointment_detail', request_id=request_id)

	conversation = None
	if appointment_request.status == AppointmentRequest.Status.ACCEPTED:
		conversation = DoctorConversation.objects.filter(
			patient=appointment_request.patient,
			doctor=appointment_request.doctor,
		).first()
	return render(request, 'myapp/appointment_detail.html', {
		'appointment_request': appointment_request,
		'is_doctor': is_doctor,
		'conversation': conversation,
	})


def message_inbox(request):
	if not request.user.is_authenticated:
		return redirect('login')
	if user_is_doctor(request.user):
		return redirect('doctor_portal')
	patient, _ = PatientProfile.objects.get_or_create(user=request.user)
	appointment_requests = AppointmentRequest.objects.filter(patient=patient).select_related('doctor__user')
	expire_pending_appointment_requests(appointment_requests)
	conversations = DoctorConversation.objects.filter(patient=patient).select_related('doctor__user').prefetch_related('messages')
	return render(request, 'myapp/messages_inbox.html', {
		'conversations': conversations,
		'appointment_requests': appointment_requests,
	})


def doctor_portal(request):
	if not request.user.is_authenticated:
		return redirect('login')
	if not user_is_doctor(request.user):
		return redirect('message_inbox')
	doctor = get_object_or_404(DoctorProfile.objects.select_related('user'), user=request.user)
	if doctor.status == DoctorProfile.Status.OFFLINE:
		doctor.status = DoctorProfile.Status.FREE
		doctor.save(update_fields=['status'])
	appointment_requests = AppointmentRequest.objects.filter(doctor=doctor).select_related('patient__user')
	expire_pending_appointment_requests(appointment_requests)
	conversations = DoctorConversation.objects.filter(doctor=doctor).select_related('patient__user').prefetch_related('messages')
	return render(request, 'myapp/doctor_portal.html', {
		'doctor': doctor,
		'conversations': conversations,
		'appointment_requests': appointment_requests,
	})


def conversation_detail(request, conversation_id):
	conversation = get_object_or_404(
		DoctorConversation.objects.select_related('doctor__user', 'patient__user'),
		pk=conversation_id,
	)
	is_doctor = conversation.doctor.user_id == request.user.pk
	is_patient = conversation.patient.user_id == request.user.pk
	if not request.user.is_authenticated:
		return redirect('login')
	if not (is_doctor or is_patient):
		return redirect('message_inbox')

	pharmacies = Pharmacy.objects.filter(is_open=True).order_by('name')
	medicines = Medicine.objects.filter(is_available=True, pharmacies__is_open=True).distinct().order_by('name')
	form = DoctorMessageForm(request.POST or None, pharmacies=pharmacies, medicines=medicines)
	if request.method == 'POST':
		if not is_doctor:
			body = (request.POST.get('body') or '').strip()
			if body:
				DoctorMessage.objects.create(conversation=conversation, sender=request.user, body=body)
				conversation.save(update_fields=['updated_at'])
				return redirect('conversation_detail', conversation_id=conversation.pk)
			messages.error(request, 'Напишите сообщение.')
		elif form.is_valid():
			DoctorMessage.objects.create(
				conversation=conversation,
				sender=request.user,
				body=form.cleaned_data['body'],
				medicine=form.cleaned_data['medicine'],
				pharmacy=form.cleaned_data['pharmacy'],
			)
			conversation.save(update_fields=['updated_at'])
			return redirect('conversation_detail', conversation_id=conversation.pk)

	return render(request, 'myapp/conversation.html', {
		'conversation': conversation,
		'messages_list': conversation.messages.select_related('sender', 'medicine', 'pharmacy'),
		'is_doctor': is_doctor,
		'form': form,
		'pharmacies': pharmacies,
	})


@directory_cache
def doctor_directory(request):
	search_query = (request.GET.get('q') or '').strip()
	specialty_filter = request.GET.get('specialty') or ''
	status_filter = request.GET.get('status') or ''
	min_price = request.GET.get('min_price')
	max_price = request.GET.get('max_price')
	doctor_sorts = {
		'rating': ('-rating', '-reviews_count', '-consultation_price', 'user__username'),
		'price_asc': ('consultation_price', '-rating'),
		'price_desc': ('-consultation_price', '-rating'),
		'reviews': ('-reviews_count', '-rating'),
		'name': ('user__last_name', 'user__first_name', 'user__username'),
	}
	sort = request.GET.get('sort') if request.GET.get('sort') in doctor_sorts else 'rating'
	queryset = DoctorProfile.objects.select_related('user').order_by(*doctor_sorts[sort])
	if status_filter:
		queryset = queryset.filter(status=status_filter)
	if min_price:
		try:
			queryset = queryset.filter(consultation_price__gte=float(min_price))
		except ValueError:
			pass
	if max_price:
		try:
			queryset = queryset.filter(consultation_price__lte=float(max_price))
		except ValueError:
			pass
	doctors = list(queryset)
	if specialty_filter:
		specialty_term = specialty_filter.casefold()
		doctors = [
			doctor for doctor in doctors
			if any(specialty_term in specialty.casefold() for specialty in doctor.specialty_list)
		]
	if search_query:
		search_term = search_query.casefold()
		doctors = [
			doctor for doctor in doctors
			if search_term in ' '.join((
				doctor.user.username,
				doctor.user.first_name,
				doctor.user.last_name,
				*doctor.specialty_list,
			)).casefold()
		]
	return render(request, 'myapp/doctor_directory.html', {
		'doctors': doctors,
		'query': search_query,
		'specialty_filter': specialty_filter,
		'status_filter': status_filter,
		'min_price': min_price or '',
		'max_price': max_price or '',
		'sort': sort,
		'favorite_ids': set(
			Favorite.objects.filter(user=request.user).values_list('doctor_id', flat=True)
		) if request.user.is_authenticated else set(),
		'available_specialties': sorted({specialty for doctor in DoctorProfile.objects.all() for specialty in doctor.specialty_list}),
		'available_statuses': DoctorProfile.Status.choices,
	})


@directory_cache
def pharmacy_directory(request):
	pharmacy_sorts = {
		'rating': ('-rating', '-review_count', 'name'),
		'name': ('name',),
		'reviews': ('-review_count', '-rating'),
	}
	sort = request.GET.get('sort') if request.GET.get('sort') in pharmacy_sorts else 'rating'
	queryset = Pharmacy.objects.filter(is_open=True)
	search_query = (request.GET.get('q') or '').strip()
	if search_query:
		queryset = queryset.filter(name__icontains=search_query) | queryset.filter(address__icontains=search_query)
	queryset = queryset.order_by(*pharmacy_sorts[sort])
	return render(request, 'myapp/pharmacies.html', {
		'pharmacies': queryset,
		'query': search_query,
		'sort': sort,
	})


@directory_cache
def pharmacy_detail(request, pharmacy_id):
	pharmacy = get_object_or_404(Pharmacy, pk=pharmacy_id, is_open=True)
	medicines = list(pharmacy.medicines.filter(is_available=True).order_by('name'))
	return render(request, 'myapp/pharmacy_detail.html', {'pharmacy': pharmacy, 'medicines': medicines})


def profile(request):
	if not request.user.is_authenticated:
		return redirect('login')

	patient, created = PatientProfile.objects.get_or_create(user=request.user)

	return render(request, 'myapp/profile.html', {
		'patient': patient,
		'orders': PharmacyOrder.objects.filter(user=request.user).select_related('pharmacy').order_by('-created_at')[:12],
		'favorites': Favorite.objects.filter(user=request.user).select_related('doctor__user')[:12],
	})

def global_search(request):
	"""JSON для палитры ⌘K: врачи, аптеки, лекарства (лекарства — только
	для авторизованных, каталог требует входа)."""
	query = (request.GET.get('q') or '').strip()
	payload = {'doctors': [], 'pharmacies': [], 'medicines': []}
	if len(query) < 2:
		return JsonResponse(payload)

	term = query.casefold()
	for doctor in DoctorProfile.objects.select_related('user').all()[:400]:
		haystack = ' '.join((
			doctor.user.username,
			doctor.user.first_name,
			doctor.user.last_name,
			doctor.specialty,
			*doctor.specialty_list,
		)).casefold()
		if term in haystack:
			payload['doctors'].append({
				'title': doctor.get_full_name(),
				'sub': f'{doctor.primary_specialty} · ★ {doctor.rating}',
				'url': reverse('doctor_detail', args=[doctor.pk]),
			})
		if len(payload['doctors']) >= 5:
			break

	for pharmacy in Pharmacy.objects.filter(is_open=True).filter(
		Q(name__icontains=query) | Q(address__icontains=query)
	)[:5]:
		payload['pharmacies'].append({
			'title': pharmacy.name,
			'sub': pharmacy.address,
			'url': reverse('pharmacy_detail', args=[pharmacy.pk]),
		})

	if request.user.is_authenticated:
		for medicine in Medicine.objects.filter(name__icontains=query, is_available=True)[:5]:
			pharmacy = Pharmacy.objects.filter(is_open=True, medicines=medicine).first()
			payload['medicines'].append({
				'title': medicine.name,
				'sub': f'{medicine.get_dosage_form_display()} · {medicine.price} ₽',
				'url': reverse('pharmacy_detail', args=[pharmacy.pk]) if pharmacy else reverse('pharmacy_directory'),
			})

	return JsonResponse(payload)


@login_required
def doctor_favorite(request, doctor_id):
	"""Добавить / убрать врача из избранного (POST со «звёздочкой»)."""
	if request.method != 'POST':
		return redirect('doctor_directory')
	doctor = get_object_or_404(DoctorProfile, pk=doctor_id)
	favorite, created = Favorite.objects.get_or_create(user=request.user, doctor=doctor)
	if created:
		messages.success(request, f'Врач {doctor.get_full_name()} добавлен в избранное.')
	else:
		favorite.delete()
		messages.success(request, f'Врач {doctor.get_full_name()} убран из избранного.')

	return_url = request.POST.get('next') or ''
	if return_url and url_has_allowed_host_and_scheme(
		return_url,
		allowed_hosts={request.get_host()},
		require_https=request.is_secure(),
	):
		return redirect(return_url)
	return redirect('doctor_detail', doctor_id=doctor.pk)


def robots_txt(request):
	lines = [
		'User-agent: *',
		'Allow: /',
		'Disallow: /admin/',
		'Disallow: /cart/',
		'Disallow: /profile/',
		f'Sitemap: {request.build_absolute_uri(reverse("sitemap_xml"))}',
	]
	return HttpResponse('\n'.join(lines), content_type='text/plain; charset=utf-8')


def sitemap_xml(request):
	routes = [
		('home', '1.0'),
		('doctor_directory', '0.9'),
		('pharmacy_directory', '0.9'),
		('register', '0.5'),
		('login', '0.5'),
	]
	today = timezone.now().date().isoformat()
	entries = ''.join(
		f'<url><loc>{request.build_absolute_uri(reverse(name))}</loc>'
		f'<lastmod>{today}</lastmod><priority>{priority}</priority></url>'
		for name, priority in routes
	)
	xml = (
		'<?xml version="1.0" encoding="UTF-8"?>'
		'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
		f'{entries}</urlset>'
	)
	return HttpResponse(xml, content_type='application/xml; charset=utf-8')

