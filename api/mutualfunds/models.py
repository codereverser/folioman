from django.conf import settings
from django.db import models


class AMC(models.Model):
    """Mutual Fund Asset Management Company (AMC)"""

    name = models.CharField(max_length=128, unique=True)
    description = models.TextField(null=True, blank=True)
    code = models.CharField(max_length=64, unique=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "AMC"
        verbose_name_plural = "AMCs"


class FundCategory(models.Model):
    """Fund Category (EQUITY, DEBT etc)"""

    class MainCategory(models.TextChoices):
        EQUITY = "EQUITY"
        DEBT = "DEBT"
        HYBRID = "HYBRID"
        OTHER = "OTHER"

    type = models.CharField(max_length=8, choices=MainCategory.choices, default=MainCategory.EQUITY)
    subtype = models.CharField(max_length=64)

    def __str__(self):
        return f"{self.type} - {self.subtype}"

    class Meta:
        verbose_name = "FundCategory"
        verbose_name_plural = "Fund Categories"


class FundScheme(models.Model):
    """Mutual fund schemes"""

    class SchemePlan(models.TextChoices):
        REGULAR = "REGULAR"
        DIRECT = "DIRECT"

    sid = models.IntegerField(unique=True, help_text="Source ID for scheme")
    name = models.CharField(max_length=512, db_index=True)
    amc = models.ForeignKey(AMC, models.CASCADE, related_name="funds")
    rta = models.CharField(max_length=12, null=True, blank=True)
    category = models.ForeignKey(
        FundCategory, models.PROTECT, blank=True, null=True, related_name="funds"
    )
    plan = models.CharField(max_length=8, choices=SchemePlan.choices, default=SchemePlan.REGULAR)
    rta_code = models.CharField(max_length=32)
    amc_code = models.CharField(max_length=32, db_index=True)
    amfi_code = models.CharField(max_length=8, null=True, blank=True, db_index=True)
    isin = models.CharField(max_length=16, db_index=True)
    start_date = models.DateField(null=True, blank=True, db_index=True)
    end_date = models.DateField(null=True, blank=True, db_index=True)
    created = models.DateTimeField(auto_now=False, auto_now_add=True)
    modified = models.DateTimeField(auto_now=True, auto_now_add=False)

    def __str__(self):
        return f"{self.name} - {self.plan}"

    class Meta:
        indexes = [
            models.Index(fields=["amc_id", "rta_code"], name="idx_rta_code_amc_id"),
            models.Index(fields=["rta", "rta_code"], name="idx_rta_code_rta"),
        ]
        verbose_name = "Fund Scheme"
        verbose_name_plural = "Fund Schemes"


class NAVHistory(models.Model):
    scheme = models.ForeignKey(FundScheme, models.CASCADE)
    date = models.DateField()
    nav = models.DecimalField(max_digits=15, decimal_places=4)

    class Meta:
        unique_together = ("scheme_id", "date")


class Portfolio(models.Model):
    """User Portfolio"""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="portfolios"
    )
    name = models.CharField(max_length=256)
    email = models.EmailField(unique=True)
    pan = models.CharField(max_length=10, null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user_id', 'name'], name='unique_user_name')
        ]

    def __str__(self):
        return self.name


class Folio(models.Model):
    """Mutual Fund Folio"""

    amc = models.ForeignKey(AMC, models.PROTECT)
    portfolio = models.ForeignKey(Portfolio, on_delete=models.CASCADE, related_name="folios")
    number = models.CharField(max_length=128, unique=True)
    pan = models.CharField(max_length=10, null=True, blank=True)
    kyc = models.BooleanField(default=False)
    pan_kyc = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.portfolio.name} - {self.number}"


class FolioScheme(models.Model):
    """Track schemes inside a folio"""

    scheme = models.ForeignKey(FundScheme, models.PROTECT, related_name="schemes")
    folio = models.ForeignKey(Folio, related_name="schemes", on_delete=models.CASCADE)
    valuation = models.DecimalField(max_digits=20, decimal_places=2, null=True)
    xirr = models.DecimalField(max_digits=20, decimal_places=4, null=True)
    valuation_date = models.DateField(null=True)
    created = models.DateTimeField(auto_now=False, auto_now_add=True)
    modified = models.DateTimeField(auto_now=True, auto_now_add=False)

    def __str__(self):
        return f"{self.scheme.name} - {self.folio.number}"


class Transaction(models.Model):
    """Transactions inside a folio scheme"""

    class OrderType(models.TextChoices):
        BUY = "Buy"
        REINVEST = "Reinvest"
        REDEEM = "Redeem"
        SWITCH = "Switch"

    scheme = models.ForeignKey(FolioScheme, models.CASCADE, related_name="transactions")
    date = models.DateField()
    description = models.TextField()
    order_type = models.CharField(max_length=8, choices=OrderType.choices)
    sub_type = models.CharField(
        max_length=32, help_text="Order type as classified by casparser", null=True, blank=True
    )
    amount = models.DecimalField(max_digits=20, decimal_places=2)
    nav = models.DecimalField(max_digits=15, decimal_places=4)
    units = models.DecimalField(max_digits=20, decimal_places=3)
    balance = models.DecimalField(max_digits=40, decimal_places=3)
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    @classmethod
    def get_order_type(cls, description, amount):
        if "switch" in description.lower():
            return cls.OrderType.SWITCH
        elif "Additional Allotment Appln" in description:
            return cls.OrderType.BUY
        elif float(amount) > 0:
            if "reinvest" in description.lower():
                return cls.OrderType.REINVEST
            return cls.OrderType.BUY
        return cls.OrderType.REDEEM

    def __str__(self):
        return f"{self.order_type} @ {self.amount} for {self.units} units"

    class Meta:
        ordering = ("date",)


class DailyValue(models.Model):
    """Track daily total of amount invested per scheme/folio/portfolio"""

    date = models.DateField(db_index=True)
    invested = models.DecimalField(max_digits=30, decimal_places=2)
    value = models.DecimalField(max_digits=30, decimal_places=2)

    class Meta:
        abstract = True


class SchemeValue(DailyValue):
    scheme = models.ForeignKey(FolioScheme, models.CASCADE, related_name="values")
    avg_nav = models.DecimalField(max_digits=30, decimal_places=10, default=0.0)
    nav = models.DecimalField(max_digits=15, decimal_places=4)
    balance = models.DecimalField(max_digits=20, decimal_places=3)

    class Meta:
        unique_together = ("scheme_id", "date")
        get_latest_by = ("date",)


class FolioValue(DailyValue):
    folio = models.ForeignKey(Folio, models.CASCADE, related_name="values")

    class Meta:
        unique_together = ("folio_id", "date")
        get_latest_by = ("date",)


class PortfolioValue(DailyValue):
    portfolio = models.ForeignKey(Portfolio, models.CASCADE, related_name="values")
    xirr = models.DecimalField(max_digits=30, decimal_places=2, null=True, blank=True)
    live_xirr = models.DecimalField(max_digits=30, decimal_places=2, null=True, blank=True)

    class Meta:
        unique_together = ("portfolio_id", "date")
        get_latest_by = ("date",)
