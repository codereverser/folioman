"""Bulk import various DailyValue models"""

import logging
import traceback

from django.db.transaction import savepoint, savepoint_commit, savepoint_rollback
from import_export.fields import Field
from import_export.instance_loaders import ModelInstanceLoader
from import_export.resources import ModelResource
from import_export.results import RowResult
from import_export.utils import atomic_if_using_transaction
from import_export.widgets import DateWidget
import pandas as pd

from folioman.models import FolioValue, PortfolioValue, SchemeValue

logger = logging.getLogger(__name__)


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

    def import_data_inner(
        self, dataset, dry_run, raise_errors, using_transactions, collect_failed_rows, **kwargs
    ):
        result = self.get_result_class()()
        result.diff_headers = self.get_diff_headers()
        result.total_rows = len(dataset)

        if using_transactions:
            # when transactions are used we want to create/update/delete object
            # as transaction will be rolled back if dry_run is set
            sp1 = savepoint()

        try:
            with atomic_if_using_transaction(using_transactions):
                self.before_import(dataset, using_transactions, dry_run, **kwargs)
        except Exception as e:
            logger.debug(e, exc_info=e)
            tb_info = traceback.format_exc()
            result.append_base_error(self.get_error_result_class()(e, tb_info))
            if raise_errors:
                raise

        instance_loader = self._meta.instance_loader_class(self, dataset)

        # Update the total in case the dataset was altered by before_import()
        result.total_rows = len(dataset)

        if collect_failed_rows:
            result.add_dataset_headers(dataset.headers)

        for i, row in enumerate(dataset.dict, 1):
            with atomic_if_using_transaction(using_transactions and not self._meta.use_bulk):
                row_result = self.import_row(
                    row,
                    instance_loader,
                    using_transactions=using_transactions,
                    dry_run=dry_run,
                    row_number=i,
                    raise_errors=raise_errors,
                    **kwargs
                )
            result.increment_row_result_total(row_result)

            if row_result.errors:
                if collect_failed_rows:
                    result.append_failed_row(row, row_result.errors[0])
                if raise_errors:
                    raise row_result.errors[-1].error
            elif row_result.validation_error:
                result.append_invalid_row(i, row, row_result.validation_error)
                if collect_failed_rows:
                    result.append_failed_row(row, row_result.validation_error)
                if raise_errors:
                    raise row_result.validation_error
            if row_result.import_type != RowResult.IMPORT_TYPE_SKIP or self._meta.report_skipped:
                result.append_row_result(row_result)

        if self._meta.use_bulk:
            # bulk persist any instances which are still pending
            with atomic_if_using_transaction(using_transactions):
                self.bulk_create(using_transactions, dry_run, raise_errors)
                self.bulk_update(using_transactions, dry_run, raise_errors)
                self.bulk_delete(using_transactions, dry_run, raise_errors)

        try:
            with atomic_if_using_transaction(using_transactions):
                self.after_import(dataset, result, using_transactions, dry_run, **kwargs)
        except Exception as e:
            logger.debug(e, exc_info=e)
            tb_info = traceback.format_exc()
            result.append_base_error(self.get_error_result_class()(e, tb_info))
            if raise_errors:
                raise

        if using_transactions:
            if dry_run or result.has_errors():
                savepoint_rollback(sp1)
            else:
                savepoint_commit(sp1)

        return result


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
