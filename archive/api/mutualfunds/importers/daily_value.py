"""Bulk import various DailyValue models"""

import pandas as pd
from import_export.fields import Field
from import_export.instance_loaders import ModelInstanceLoader
from import_export.resources import ModelResource
from import_export.widgets import DateWidget

from mutualfunds.models import FolioValue, PortfolioValue, SchemeValue


class CustomDateWidget(DateWidget):
    """DateWidget that supports pd.Timestamp"""

    def clean(self, value, row=None, *args, **kwargs):
        if isinstance(value, pd.Timestamp):
            return value.date()
        return super().clean(value, row=row, *args, **kwargs)


class DailyValueInstanceLoader(ModelInstanceLoader):
    """
    Loads all possible model instances in dataset avoid hitting database for
    every ``get_instance`` call.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        pk_field_names = self.resource.get_import_id_fields()
        self.pk_fields = [self.resource.fields[x] for x in pk_field_names]
        self.fkey_tables = [x.split("__")[0] for x in self.resource.fields.keys() if "__" in x]

        self.main_pk_field = self.pk_fields[0]

        ids = set([self.main_pk_field.clean(row) for row in self.dataset.dict])
        qs = (
            self.get_queryset()
            .select_related(*self.fkey_tables)
            .filter(**{"%s__in" % self.main_pk_field.attribute: ids})
        )

        self.all_instances = {
            tuple(pk_field.get_value(instance) for pk_field in self.pk_fields): instance
            for instance in qs
        }

    def get_instance(self, row):
        key = tuple(pk_field.clean(row) for pk_field in self.pk_fields)
        return self.all_instances.get(key)


class DailyValueResource(ModelResource):

    ModelResource.WIDGETS_MAP.update(
        {
            "DateField": CustomDateWidget,
        }
    )

    class Meta:
        skip_unchanged = True
        use_bulk = True
        skip_diff = True
        instance_loader_class = DailyValueInstanceLoader
        batch_size = 3000


class SchemeValueResource(DailyValueResource):
    scheme__id = Field(attribute="scheme_id", column_name="scheme__id")

    class Meta:
        import_id_fields = ("scheme__id", "date")
        fields = ("invested", "avg_nav", "balance", "nav", "value", "scheme__id", "date")
        model = SchemeValue


class FolioValueResource(DailyValueResource):
    folio__id = Field(attribute="folio_id", column_name="folio__id")

    class Meta:
        import_id_fields = ("folio__id", "date")
        fields = ("invested", "value", "folio__id", "date")
        model = FolioValue


class PortfolioValueResource(DailyValueResource):
    portfolio__id = Field(attribute="portfolio_id", column_name="portfolio__id")

    class Meta:
        import_id_fields = ("portfolio__id", "date")
        fields = ("invested", "value", "portfolio__id", "date")
        model = PortfolioValue
