import unittest

from services.registration_service import normalize_phone_number, normalize_student_name


class TestRegistrationInputValidation(unittest.TestCase):
    def test_name_is_title_cased(self):
        self.assertEqual(normalize_student_name("aung aung"), "Aung Aung")
        self.assertEqual(normalize_student_name("MYO MIN"), "Myo Min")

    def test_name_whitespace_is_normalized(self):
        self.assertEqual(normalize_student_name("  aung   aung  "), "Aung Aung")

    def test_english_name_with_punctuation_is_supported(self):
        self.assertEqual(normalize_student_name("o'neill"), "O'Neill")
        self.assertEqual(normalize_student_name("aung-aung"), "Aung-Aung")

    def test_non_english_or_invalid_name_is_rejected(self):
        for name in ("", "   ", "အောင်အောင်", "Aung123", "@aung", "A"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    normalize_student_name(name)

    def test_local_phone_format_is_normalized(self):
        self.assertEqual(normalize_phone_number("09 123 456 789"), "09123456789")
        self.assertEqual(normalize_phone_number("09-123-456-789"), "09123456789")

    def test_international_phone_format_is_supported(self):
        self.assertEqual(normalize_phone_number("+95 9 123 456 789"), "+959123456789")
        self.assertEqual(normalize_phone_number("959123456789"), "959123456789")

    def test_invalid_phone_is_rejected(self):
        for phone in ("", "123", "09abcdef", "09000", "+1 202 555 1234"):
            with self.subTest(phone=phone):
                with self.assertRaises(ValueError):
                    normalize_phone_number(phone)


if __name__ == "__main__":
    unittest.main()
