from django.contrib import admin

from .models import (
	DoctorProfile,
	DoctorReview,
	Medicine,
	MedicineCategory,
	MedicineOrder,
	MedicalPrescription,
	PaymentCard,
	PharmacyOrder,
	PharmacyOrderItem,
	ShoppingCartItem,
	PatientDiscount,
	PatientProfile,
	PharmacistProfile,
	Pharmacy,
	PrescriptionItem,
	UserProfile,
)


admin.site.register(UserProfile)
admin.site.register(PatientProfile)
admin.site.register(PatientDiscount)
admin.site.register(DoctorProfile)
admin.site.register(DoctorReview)
admin.site.register(PaymentCard)


@admin.register(Pharmacy)
class PharmacyAdmin(admin.ModelAdmin):
	list_display = ['name', 'address', 'is_open']
	filter_horizontal = ['medicines']


admin.site.register(PharmacistProfile)
admin.site.register(MedicineCategory)


@admin.register(Medicine)
class MedicineAdmin(admin.ModelAdmin):
	list_display = ['name', 'dosage_form', 'price', 'is_available']
	list_filter = ['dosage_form', 'is_available', 'categories']
	search_fields = ['name', 'active_ingredient']
	filter_horizontal = ['categories']


admin.site.register(MedicalPrescription)
admin.site.register(PrescriptionItem)
admin.site.register(MedicineOrder)


class PharmacyOrderItemInline(admin.TabularInline):
	model = PharmacyOrderItem
	extra = 0
	readonly_fields = ['medicine', 'quantity', 'unit_price']


@admin.register(PharmacyOrder)
class PharmacyOrderAdmin(admin.ModelAdmin):
	list_display = ['id', 'user', 'pharmacy', 'status', 'payment_status', 'total_amount', 'created_at']
	list_filter = ['status', 'payment_status', 'pharmacy', 'created_at']
	search_fields = ['user__username', 'id']
	readonly_fields = [
		'user', 'pharmacy', 'discount_percent', 'total_amount',
		'payment_method', 'payment_status', 'created_at',
	]
	inlines = [PharmacyOrderItemInline]


@admin.register(ShoppingCartItem)
class ShoppingCartItemAdmin(admin.ModelAdmin):
	list_display = ['user', 'pharmacy', 'medicine', 'quantity', 'created_at']
	list_filter = ['pharmacy', 'created_at']
	search_fields = ['user__username', 'medicine__name']
