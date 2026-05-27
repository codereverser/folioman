from django.contrib import admin

from .models import AMC, FundScheme, Portfolio, Folio, FolioScheme


@admin.register(AMC)
class AMCAdmin(admin.ModelAdmin):
    list_display = ("name", "code")


@admin.register(FundScheme)
class FundSchemeAdmin(admin.ModelAdmin):
    list_display = ("name", "amc", "category", "plan", "amfi_code", "isin")
    list_filter = ("amc", "plan", "category")
    search_fields = ("name",)


@admin.register(Portfolio)
class PortfolioAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "pan")


@admin.register(Folio)
class FolioAdmin(admin.ModelAdmin):
    list_display = ("number", "amc", "pan", "portfolio")
    search_fields = ("number", "amc")


@admin.register(FolioScheme)
class FolioScheme(admin.ModelAdmin):
    list_display = ("scheme", "folio")
    autocomplete_fields = ("scheme", "folio")
