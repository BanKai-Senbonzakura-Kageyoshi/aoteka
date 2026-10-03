from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.utils import timezone

from .models import DoctorReview, Medicine, Pharmacy


class SymptomForm(forms.Form):
    description = forms.CharField(
        label='Что вас беспокоит?',
        widget=forms.Textarea(attrs={'rows': 4}),
    )
    consent_to_ai = forms.BooleanField(
        required=False,
        label='Я согласен передать описание симптомов Google Gemini для справочного выбора специальности.',
    )


class RegisterForm(UserCreationForm):
    first_name = forms.CharField(required=False, max_length=150, label='Как к вам обращаться')
    last_name = forms.CharField(required=False, max_length=150, label='Фамилия')
    primary_role = forms.ChoiceField(
        choices=[('PATIENT', 'Пациент'), ('DOCTOR', 'Врач')],
        widget=forms.RadioSelect,
        label='Я регистрируюсь как',
    )
    specialty = forms.CharField(
        required=False,
        max_length=100,
        label='Основная специальность врача',
    )
    specialties = forms.CharField(
        required=False,
        label='Специальности врача (через запятую, от 1 до 10)',
        widget=forms.TextInput(attrs={'placeholder': 'Кардиолог, Терапевт'}),
    )

    def __init__(self, *args, role=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.registration_role = {'patient': 'PATIENT', 'doctor': 'DOCTOR'}.get(role)
        if self.registration_role:
            self.fields.pop('primary_role')
            if self.registration_role == 'PATIENT':
                self.fields.pop('specialty')
                self.fields.pop('specialties')
            else:
                self.fields.pop('specialty')

    def clean(self):
        cleaned_data = super().clean()
        role = self.registration_role or cleaned_data.get('primary_role')
        if role == 'DOCTOR':
            specialties = [name.strip() for name in str(cleaned_data.get('specialties', '')).split(',') if name.strip()]
            if not specialties and not cleaned_data.get('specialty', '').strip():
                self.add_error('specialty', 'Укажите хотя бы одну специальность.')
            if len(specialties) > 10:
                self.add_error('specialties', 'Можно выбрать не более 10 специальностей.')
            if cleaned_data.get('specialty') and not specialties:
                specialties = [cleaned_data['specialty'].strip()]
            cleaned_data['specialty_choices'] = specialties or [cleaned_data.get('specialty', '').strip()]
        cleaned_data['registration_role'] = role
        return cleaned_data


class DoctorReviewForm(forms.ModelForm):
    class Meta:
        model = DoctorReview
        fields = ['rating', 'comment']
        labels = {
            'rating': 'Оценка от 1 до 5',
            'comment': 'Отзыв',
        }


class DoctorMessageForm(forms.Form):
    body = forms.CharField(
        required=False,
        label='Сообщение',
        widget=forms.Textarea(attrs={'rows': 3, 'placeholder': 'Напишите пациенту...'}),
    )
    pharmacy = forms.ModelChoiceField(
        queryset=Pharmacy.objects.none(),
        required=False,
        label='Аптека для рекомендации',
    )
    medicine = forms.ModelChoiceField(
        queryset=Medicine.objects.none(),
        required=False,
        label='Лекарство',
    )

    def __init__(self, *args, pharmacies=None, medicines=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['pharmacy'].queryset = pharmacies if pharmacies is not None else Pharmacy.objects.none()
        self.fields['medicine'].queryset = medicines if medicines is not None else Medicine.objects.none()

    def clean(self):
        cleaned_data = super().clean()
        pharmacy = cleaned_data.get('pharmacy')
        medicine = cleaned_data.get('medicine')
        body = (cleaned_data.get('body') or '').strip()

        if bool(pharmacy) != bool(medicine):
            self.add_error('medicine' if pharmacy else 'pharmacy', 'Выберите и аптеку, и лекарство.')
        elif medicine and not medicine.pharmacies.filter(pk=pharmacy.pk, is_open=True).exists():
            self.add_error('medicine', 'Этого лекарства нет в выбранной аптеке.')
        if not body and not medicine:
            self.add_error('body', 'Напишите сообщение или выберите лекарство для рекомендации.')
        cleaned_data['body'] = body
        return cleaned_data


class CartAddForm(forms.Form):
    pharmacy_id = forms.IntegerField(min_value=1)
    medicine_id = forms.IntegerField(min_value=1)
    quantity = forms.IntegerField(min_value=1, max_value=99, initial=1)


class CartQuantityForm(forms.Form):
    quantity = forms.IntegerField(min_value=1, max_value=99)


class CardPaymentForm(forms.Form):
    """Оплата заказа картой на странице оплаты.

    Учебная форма: номер и CVC никуда не сохраняются и не передаются банку.
    Принимается любая комбинация цифр — реальной проверки у банка нет."""

    card_number = forms.CharField(
        label='Номер карты',
        min_length=4,
        max_length=23,
        widget=forms.TextInput(attrs={
            'inputmode': 'numeric',
            'autocomplete': 'cc-number',
            'maxlength': '23',
            'placeholder': '0000 0000 0000 0000',
            'data-card-field': 'number',
        }),
        error_messages={'required': 'Введите номер карты.', 'min_length': 'Слишком короткий номер карты.'},
    )
    exp_month = forms.TypedChoiceField(
        coerce=int,
        label='Месяц',
        choices=[],
        widget=forms.Select(attrs={'autocomplete': 'cc-exp-month', 'data-card-field': 'month'}),
        error_messages={'invalid_choice': 'Укажите месяц.'},
    )
    exp_year = forms.TypedChoiceField(
        coerce=int,
        label='Год',
        choices=[],
        widget=forms.Select(attrs={'autocomplete': 'cc-exp-year', 'data-card-field': 'year'}),
        error_messages={'invalid_choice': 'Укажите год.'},
    )
    cardholder_name = forms.CharField(
        label='Имя владельца',
        min_length=1,
        max_length=60,
        widget=forms.TextInput(attrs={
            'autocomplete': 'cc-name',
            'placeholder': 'IVAN IVANOV',
            'data-card-field': 'holder',
        }),
        error_messages={'required': 'Укажите имя на карте.'},
    )
    cvc = forms.CharField(
        label='CVC / CVV',
        min_length=1,
        max_length=6,
        widget=forms.TextInput(attrs={
            'inputmode': 'numeric',
            'autocomplete': 'cc-csc',
            'maxlength': '6',
            'placeholder': '000',
            'data-card-field': 'cvc',
        }),
        error_messages={'required': 'Введите CVC.'},
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        now = timezone.now()
        self.fields['exp_month'].choices = [('', '—')] + [
            (str(month), f'{month:02d}') for month in range(1, 13)
        ]
        self.fields['exp_year'].choices = [('', '—')] + [
            (str(year), str(year)) for year in range(now.year, now.year + 16)
        ]

    def clean_card_number(self):
        return ''.join(self.cleaned_data['card_number'].split())

    def clean_cardholder_name(self):
        return ' '.join(self.cleaned_data['cardholder_name'].split())

    def clean(self):
        cleaned_data = super().clean()
        month = cleaned_data.get('exp_month')
        year = cleaned_data.get('exp_year')
        if month and year:
            now = timezone.now()
            if year < now.year or (year == now.year and month < now.month):
                self.add_error('exp_year', 'Срок действия карты истёк.')
        return cleaned_data