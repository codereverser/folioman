from decimal import Decimal
from django.test import TestCase
from tablib import Dataset

from mutualfunds.importers.cas import import_cas
from mutualfunds.importers.master import FundSchemeResource
from mutualfunds.models import Portfolio, Folio, FundScheme


class TestImportCas(TestCase):

    def setUp(self):
        # Mock data setup
        self.data = {
            "investor_info": {"email": "test@example.com", "name": "Test User"},
            "folios": [],
            "statement_period": {"from": "2021-01-01", "to": "2021-01-31"}
        }
        self.user_id = 1

    def test_import_cas_valid_email_name(self):
        # Test case for valid email and name
        Portfolio.objects.create(email="test@example.com", name="Test User", user_id=self.user_id)
        result = import_cas(self.data, self.user_id)
        self.assertIsNotNone(result)

        # Additional assertions
        portfolio = Portfolio.objects.get(email="test@example.com")
        self.assertEqual(portfolio.name, "Test User")
        self.assertEqual(portfolio.user_id, self.user_id)

    def test_import_cas_invalid_email(self):
        # Test case for invalid email
        self.data["investor_info"]["email"] = ""
        with self.assertRaises(ValueError):
            import_cas(self.data, self.user_id)

        # Additional assertions
        self.assertFalse(Portfolio.objects.filter(email="").exists())

    def test_import_cas_new_folio_creation(self):
        # Test case for creating a new folio
        self.data["folios"] = [{
            "folio": "123",
            "KYC": "OK",
            "PANKYC": "OK",
            "PAN": "ABCDE1234F",
            "schemes": [{
                "advisor": "INA000006651",
                "amfi": "120503",
                "close": 146.556,
                "close_calculated": 146.556,
                "isin": "INF846K01EW2",
                "open": 0.0,
                "rta": "KFINTECH",
                "rta_code": "128TSDGG",
                "scheme": "Axis ELSS Tax Saver Fund - Direct Growth - ISIN: INF846K01EW2",
                "transactions": [
                    {
                        "amount": 1000.0,
                        "balance": 23.711,
                        "date": "2017-09-20",
                        "description": "Purchase",
                        "dividend_rate": "",
                        "nav": 42.1747,
                        "type": "PURCHASE",
                        "units": 23.711
                    }
                ]
            }]
        }]
        import_cas(self.data, self.user_id)
        self.assertEqual(Folio.objects.count(), 1)

        # Additional assertions
        folio = Folio.objects.get(number="123")
        self.assertTrue(folio.kyc)
        self.assertTrue(folio.pan_kyc)
        self.assertEqual(folio.pan, "ABCDE1234F")

        # Additional assertions for transactions and schemes
        schemes = folio.schemes.all()
        self.assertEqual(schemes.count(), 1)

        scheme = schemes.first()
        self.assertEqual(scheme.scheme_id, FundScheme.objects.get(isin="INF846K01EW2").id)

        transactions = scheme.transactions.all()
        self.assertEqual(transactions.count(), 1)

        transaction = transactions.first()
        self.assertEqual(transaction.amount, Decimal('1000.0'))
        self.assertEqual(transaction.balance, Decimal('23.711'))
        self.assertEqual(transaction.nav, Decimal('42.1747'))
        self.assertEqual(transaction.units, Decimal('23.711'))
        self.assertEqual(transaction.description, "Purchase")
    def test_import_cas_missing_kyc(self):
        # Test case for missing KYC
        self.data["folios"] = [{
            "folio": "124",
            "PANKYC": "OK",
            "KYC": "",
            "PAN": "ABCDE1234F",
            "schemes": [{
                "advisor": "INA000006651",
                "amfi": "120503",
                "close": 146.556,
                "close_calculated": 146.556,
                "isin": "INF846K01EW2",
                "open": 0.0,
                "rta": "KFINTECH",
                "rta_code": "128TSDGG",
                "scheme": "Axis ELSS Tax Saver Fund - Direct Growth - ISIN: INF846K01EW2",
                "transactions": [
                    {
                        "amount": 1000.0,
                        "balance": 23.711,
                        "date": "2017-09-20",
                        "description": "Purchase",
                        "dividend_rate": "",
                        "nav": 42.1747,
                        "type": "PURCHASE",
                        "units": 23.711
                    }
                ]
            }]
        }]
        import_cas(self.data, self.user_id)
        self.assertEqual(Folio.objects.count(), 1)


class TestDependencies(TestCase):

    def test_import_export(self):
        # Test django-import-export functionality
        resource = FundSchemeResource()
        dataset = Dataset(headers=["sid", "name", "rta", "plan", "rta_code", "amc_code", "amfi_code", "isin", "start_date", "end_date", "amc_id", "category_id"])
        dataset.append([1, "Test Scheme", "Test RTA", "DIRECT", "123", "456", "789", "INF123456789", "2025-01-01", "2025-12-31", 1, 1])
        result = resource.import_data(dataset, dry_run=True)
        self.assertFalse(result.has_errors(), "Import should not have errors")

    def test_tablib(self):
        # Test tablib[pandas] functionality
        dataset = Dataset(headers=["Name", "Age"])
        dataset.headers = ["Name", "Age"]
        dataset.append(["Alice", 30])
        dataset.append(["Bob", 25])
        self.assertEqual(len(dataset), 2, "Dataset should have 2 rows")

    # Additional test cases can be added here to cover other functionalities
