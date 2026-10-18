import unittest
import helpers  # noqa: F401
from pdsia.normalize import (parse_components, parse_strength_piece, parse_strength_pieces, strength_options,
                             strengths_equal, parse_form, norm_ndc, ndc_match)


class Ingredients(unittest.TestCase):
    def test_salts_stripped_but_kept_as_attribute(self):
        c = parse_components("Cefazolin Sodium Injection")[0]
        self.assertEqual((c.base, set(c.salts)), ("cefazolin", {"sodium"}))

    def test_protected_electrolytes_are_not_stripped(self):
        self.assertEqual(parse_components("Sodium Chloride Injection")[0].base, "sodium chloride")
        self.assertEqual(parse_components("Potassium Chloride")[0].base, "potassium chloride")
        self.assertNotEqual(parse_components("Sodium Chloride")[0].base, parse_components("Potassium Chloride")[0].base)

    def test_combination_is_a_set(self):
        bases = [c.base for c in parse_components("Amoxicillin and Clavulanate Potassium Tablet")]
        self.assertEqual(bases, ["amoxicillin", "clavulanate"])

    def test_synonym(self):
        self.assertEqual(parse_components("Salbutamol Sulfate")[0].base, "albuterol")


class Strengths(unittest.TestCase):
    def test_units_and_conversion(self):
        self.assertEqual(parse_strength_piece("1 g")[0].value, 1000.0)
        self.assertAlmostEqual(parse_strength_piece("0.083%")[0].value, 0.83)
        self.assertEqual(parse_strength_piece("5,000 units/mL")[0].value, 5000.0)
        self.assertEqual(parse_strength_piece("250 mg/5 mL")[0].value, 50.0)
        self.assertEqual(parse_strength_piece("2 mEq/mL")[0].dim, "meq/ml")

    def test_combination_tablet_strength(self):
        self.assertEqual([s.value for s in parse_strength_piece("875 mg/125 mg")], [875.0, 125.0])

    def test_percent_equals_mg_per_ml(self):
        a, b = parse_strength_piece("1%")[0], parse_strength_piece("10 mg/mL")[0]
        self.assertTrue(strengths_equal(a, b))

    def test_unparseable_returns_none(self):
        self.assertIsNone(parse_strength_piece("1:100,000"))
        self.assertIsNone(parse_strength_pieces(["fine", "1:100,000"]))
        self.assertIsNone(parse_strength_pieces([]))

    def test_options_pairing(self):
        two = strength_options(2, parse_strength_pieces(["10 mg/mL", "0.01 mg/mL"]))
        self.assertEqual(len(two), 1)
        self.assertIsNone(strength_options(2, parse_strength_pieces(["10 mg/mL"])))


class Forms(unittest.TestCase):
    def test_release_profile(self):
        self.assertEqual(parse_form("Tablet, Extended Release").release, "extended")
        self.assertEqual(parse_form("Tablet, Film Coated").release, "immediate")

    def test_family(self):
        self.assertEqual(parse_form("Injection, Powder, for Solution").family, "injection")
        self.assertEqual(parse_form("Powder, for Suspension").family, "oral_liquid")
        self.assertEqual(parse_form("Solution").family, "unspecified")
        self.assertIsNone(parse_form(""))


class Ndc(unittest.TestCase):
    def test_normalisation(self):
        self.assertEqual(norm_ndc("0002-1433-80"), "00002143380")
        self.assertTrue(ndc_match(["99999-1040-01"], ["99999-1040-01"]))
        self.assertTrue(ndc_match(["99999-1040"], ["99999-1040-01"]))     # product-level prefix
        self.assertFalse(ndc_match(["99999-1040-01"], ["99999-1041-01"]))
        self.assertFalse(ndc_match([], ["99999-1040-01"]))


if __name__ == "__main__":
    unittest.main()
