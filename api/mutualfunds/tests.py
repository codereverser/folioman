from django.test import TestCase

from mutualfunds.importers.cas import import_cas
from mutualfunds.models import Portfolio, Folio


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

    def test_import_cas_invalid_email(self):
        # Test case for invalid email
        self.data["investor_info"]["email"] = ""
        with self.assertRaises(ValueError):
            import_cas(self.data, self.user_id)

    def test_import_cas_new_folio_creation(self):
        # Test case for creating a new folio
        self.data["folios"] = [{
            "folio": "123",
            "KYC" : "OK",
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

    def test_import_cas_missing_kyc(self):
        # Test case for creating a new folio
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

    # Additional test cases can be added here to cover other functionalities
