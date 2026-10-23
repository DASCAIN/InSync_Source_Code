import math
import unittest

from services.visualization_service import (
    VisualizationValidationError,
    create_fallback_simulation,
    create_verified_simulation,
    validate_and_normalize_simulation,
)


class VisualizationServiceTests(unittest.TestCase):
    def test_projectile_uses_values_from_question(self):
        visual = create_verified_simulation(
            "A projectile has an initial velocity of 20 m/s at an angle of 30 degrees."
        )

        self.assertEqual(visual["archetype"], "projectile_motion")
        self.assertAlmostEqual(
            visual["plotly_spec"]["data"][0]["x"][-1],
            35.31,
            places=1,
        )
        self.assertFalse(any("launch speed" in item.lower() for item in visual["assumptions"]))
        self.assertFalse(any("launch angle" in item.lower() for item in visual["assumptions"]))

    def test_momentum_metrics_are_equation_derived(self):
        visual = create_verified_simulation(
            "A truck has loaded mass 6000 kg and unloaded mass 3000 kg, "
            "travels at 72 km/h, and experiences a braking force of 12000 N."
        )

        params = visual["simulation_spec"]["parameters"]
        self.assertEqual(params["loaded_mass"], 6000)
        self.assertEqual(params["speed_kmh"], 72)
        self.assertIn("100.0 m", visual["metrics"][1]["value"])
        self.assertEqual(visual["accuracy_status"], "verified")

    def test_kmeans_trace_count_matches_explicit_k(self):
        visual = create_verified_simulation("K-means partitions the data into k=4 clusters.")

        self.assertEqual(visual["metrics"][0]["value"], "4")
        self.assertEqual(len(visual["plotly_spec"]["data"]), 5)  # four clusters + centroids

    def test_linear_equation_is_used(self):
        visual = create_verified_simulation("For linear regression, examine the fitted line y = 2x - 3.")

        self.assertEqual(visual["metrics"][0]["value"], "2")
        self.assertEqual(visual["metrics"][1]["value"], "-3")

    def test_logistic_threshold_and_coefficients_are_extracted(self):
        visual = create_verified_simulation(
            "For logistic regression use w=2, bias=-6, and threshold=70%."
        )

        self.assertEqual(visual["metrics"][1]["value"], "0.7")
        self.assertAlmostEqual(
            visual["plotly_spec"]["layout"]["shapes"][1]["x0"],
            3.42,
            places=2,
        )
        self.assertEqual(visual["accuracy_status"], "verified")

    def test_validator_rejects_mismatched_coordinates(self):
        with self.assertRaises(VisualizationValidationError):
            validate_and_normalize_simulation({
                "engine": "plotly",
                "title": "Broken graph",
                "explanation": "Invalid coordinates.",
                "plotly_spec": {
                    "data": [{"x": [1, 2], "y": [1]}],
                    "layout": {},
                },
            })

    def test_validator_rejects_non_finite_coordinates(self):
        with self.assertRaises(VisualizationValidationError):
            validate_and_normalize_simulation({
                "engine": "plotly",
                "title": "Broken graph",
                "explanation": "Invalid coordinates.",
                "plotly_spec": {
                    "data": [{"x": [1], "y": [math.nan]}],
                    "layout": {},
                },
            })

    def test_validator_rejects_answer_spoilers(self):
        with self.assertRaises(VisualizationValidationError):
            validate_and_normalize_simulation({
                "engine": "plotly",
                "title": "The correct answer is option B",
                "explanation": "Spoiler.",
                "plotly_spec": {
                    "data": [{"x": [1], "y": [1]}],
                    "layout": {},
                },
            })

    def test_offline_fallback_refuses_unverified_generic_graph(self):
        with self.assertRaises(VisualizationValidationError):
            create_fallback_simulation("Which scientist first described this concept?")


if __name__ == "__main__":
    unittest.main()
