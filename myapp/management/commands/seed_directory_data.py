from django.core.management.base import BaseCommand
from django.contrib.auth.models import User

from myapp.models import DoctorProfile, Medicine, MedicineCategory, Pharmacy


SERIES_ROOTS = [
    'Аврора', 'Рассвет', 'Родник', 'Лаванда', 'Ирис', 'Мелисса', 'Олива', 'Мята', 'Вереск', 'Липа',
    'Ромашка', 'Шалфей', 'Календула', 'Чабрец', 'Василёк', 'Жасмин', 'Кедр', 'Сосна', 'Таволга', 'Облепиха',
    'Север', 'Волна', 'Баланс', 'Гармония', 'Сфера',
]
SERIES_MODIFIERS = ['Семейная', 'Лёгкая', 'Натуральная']
FORM_NAMES = {
    Medicine.DosageForm.TABLET: 'таблетки',
    Medicine.DosageForm.CAPSULE: 'капсулы',
    Medicine.DosageForm.SYRUP: 'сироп',
    Medicine.DosageForm.SUPPOSITORY: 'суппозитории',
    Medicine.DosageForm.AMPOULE: 'раствор в ампулах',
    Medicine.DosageForm.OINTMENT: 'мазь',
}
PACKSHOT_PALETTE = [
    ('#D85A4A', '#F8E5E1'),
    ('#4168A8', '#E7EDF8'),
    ('#C38A35', '#F7EEDC'),
    ('#80609E', '#F0EAF6'),
    ('#A44F68', '#F5E8ED'),
    ('#466E89', '#E8EFF4'),
]


class Command(BaseCommand):
    help = 'Seed the directory with demo pharmacies and doctors.'

    def handle(self, *args, **options):
        series_names = [f'{modifier} {root}' for root in SERIES_ROOTS for modifier in SERIES_MODIFIERS]
        generated_medicines = Medicine.objects.filter(name__startswith='Демо-препарат')
        generated_medicines |= Medicine.objects.filter(name__startswith='Семейная ')
        generated_medicines |= Medicine.objects.filter(name__startswith='Лёгкая ')
        generated_medicines |= Medicine.objects.filter(name__startswith='Натуральная ')
        series_labels = ['А', 'Б', 'В', 'Г', 'Д', 'Е']
        for serial, medicine in enumerate(generated_medicines.order_by('pk')):
            if medicine.dosage_form not in FORM_NAMES:
                continue
            series = series_names[serial % len(series_names)]
            series_label = series_labels[(serial // len(series_names)) % len(series_labels)]
            medicine.name = f'{series} · {FORM_NAMES[medicine.dosage_form]} · серия {series_label}'
            medicine.save(update_fields=['name'])

        for index, medicine in enumerate(Medicine.objects.order_by('pk')):
            medicine.primary_color, medicine.secondary_color = PACKSHOT_PALETTE[index % len(PACKSHOT_PALETTE)]
            medicine.save(update_fields=['primary_color', 'secondary_color'])

        MedicineCategory.objects.filter(name='Демонстрационный каталог').update(name='Аптечный ассортимент')

        pharmacy_data = [
            ('Здоровье+', 'ул. Лесная, 12', 'Аптека с широким ассортиментом и быстрым оформлением заказов.', 4.8, 126),
            ('Медицинский дом', 'пр. Мира, 34', 'Лекарства, витамины и консультации фармацевта по рецептам.', 4.7, 98),
            ('Надежная аптека', 'ул. Садовая, 8', 'Круглосуточный сервис и удобная выдача по рецептам.', 4.9, 154),
            ('Лекарь', 'ул. Пушкина, 19', 'Ассортимент по рекомендациям врачей и подбор аналогов.', 4.6, 89),
            ('Родник здоровья', 'пер. Городской, 3', 'Сеть аптек с акциями и современной доставкой.', 4.8, 143),
            ('Фармос', 'ул. Орловская, 22', 'Товары для семейного здоровья и профилактики.', 4.5, 74),
            ('Лекарство плюс', 'ул. Киевская, 41', 'Подбор лекарств под назначение и удобная корзина.', 4.7, 112),
            ('Зелёная аптека', 'ул. Ясная, 14', 'Натуральные препараты и проверенные средства.', 4.9, 160),
            ('Аптека 24', 'ул. Озёрная, 27', 'Работаем в удобное время и быстро комплектуем заказы.', 4.8, 134),
            ('Новый взгляд', 'ул. Малая, 5', 'Большой каталог и персональный подбор для семьи.', 4.6, 96),
        ]

        pharmacies = []
        for index, (name, address, description, rating, review_count) in enumerate(pharmacy_data, start=1):
            pharmacy, created = Pharmacy.objects.get_or_create(
                name=name,
                defaults={'address': address, 'description': description, 'rating': rating, 'review_count': review_count},
            )
            if not created:
                pharmacy.address = address
                pharmacy.description = description
                pharmacy.rating = rating
                pharmacy.review_count = review_count
                pharmacy.save(update_fields=['address', 'description', 'rating', 'review_count'])
            pharmacies.append(pharmacy)

        medicines = Medicine.objects.filter(is_available=True).order_by('pk')
        pharmacy_count = len(pharmacies)
        for index, medicine in enumerate(medicines):
            medicine.pharmacies.add(
                pharmacies[index % pharmacy_count],
                pharmacies[(index + pharmacy_count // 2) % pharmacy_count],
            )

        doctor_specs = [
            ('Кардиолог', ['Кардиолог', 'Терапевт']),
            ('Терапевт', ['Терапевт', 'Педиатр']),
            ('Ортопед', ['Ортопед', 'Невролог']),
            ('Невролог', ['Невролог', 'Педиатр']),
            ('Дерматолог', ['Дерматолог', 'Офтальмолог']),
            ('Педиатр', ['Педиатр', 'Терапевт']),
        ]

        for index, (main_specialty, specialties) in enumerate(doctor_specs, start=1):
            username = f'doc{index}'
            user, _ = User.objects.get_or_create(username=username, defaults={'email': f'{username}@careline.local'})
            doctor, created = DoctorProfile.objects.get_or_create(
                user=user,
                defaults={'specialty': main_specialty, 'specialties': specialties, 'status': DoctorProfile.Status.FREE, 'rating': 4.8, 'reviews_count': 22 + index},
            )
            if not created:
                doctor.specialty = main_specialty
                doctor.specialties = specialties
                doctor.status = DoctorProfile.Status.FREE
                doctor.rating = 4.8
                doctor.reviews_count = 22 + index
                doctor.save(update_fields=['specialty', 'specialties', 'status', 'rating', 'reviews_count'])

        self.stdout.write(self.style.SUCCESS('Seeded 10 pharmacies and demo doctors.'))
