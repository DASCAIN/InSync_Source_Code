"""
Visualization Service for InSync.
Universal STEM visualizer combining Plotly.js declarative scientific graphs
with high-fidelity physical motion simulations.
"""

import json
import math
import os
import re
from typing import Dict, Any, List, Optional

from openai import AsyncOpenAI

from config import get_settings
from utils.logger import get_logger

logger = get_logger(__name__)

MAX_PLOT_TRACES = 12
MAX_POINTS_PER_TRACE = 300
ALLOWED_ENGINES = {"plotly", "physical_simulation"}
ALLOWED_PHYSICAL_SIMULATIONS = {"momentum_stopping"}
ALLOWED_CONTROL_TARGETS = {
    "threshold_y", "scale_y", "shift_x", "cluster_spread", "point_marker",
    "gradient_step", "tangent_probe", "detour",
}


class VisualizationValidationError(ValueError):
    """Raised when a generated visual cannot be verified as safe and coherent."""


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _question_number(
    text: str,
    labels: List[str],
    units: Optional[List[str]] = None,
) -> Optional[float]:
    """Extract a number explicitly tied to a label, without guessing unlabeled values."""
    label_pattern = "|".join(re.escape(label) for label in labels)
    unit_pattern = "|".join(re.escape(unit) for unit in (units or []))
    suffix = rf"\s*(?:{unit_pattern})\b" if units else ""
    patterns = [
        rf"(?:{label_pattern})\s*(?:=|:|is|of)?\s*(-?\d+(?:\.\d+)?)%?{suffix}",
        rf"(-?\d+(?:\.\d+)?)%?{suffix}\s*(?:for\s+)?(?:the\s+)?(?:{label_pattern})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            try:
                return float(match.group(1))
            except (TypeError, ValueError):
                return None
    return None


def _extract_explicit_parameters(question_text: str) -> Dict[str, float]:
    """Extract only quantities stated in the question; missing values stay missing."""
    text = question_text.replace("−", "-")
    params: Dict[str, float] = {}

    threshold_match = re.search(
        r"(?:threshold|tau|τ)\s*(?:=|:|is|of)?\s*(\d+(?:\.\d+)?)\s*(%)?",
        text,
        flags=re.IGNORECASE,
    )
    if threshold_match:
        threshold = float(threshold_match.group(1))
        if threshold_match.group(2) or threshold > 1:
            threshold /= 100.0
        params["threshold"] = threshold

    for key, labels in {
        "weight": ["weight", "coefficient", "w"],
        "bias": ["bias", "intercept", "b"],
        "learning_rate": ["learning rate", "step size", "alpha", "α"],
        "loaded_mass": ["loaded mass", "loaded truck mass", "loaded vehicle mass"],
        "unloaded_mass": ["unloaded mass", "empty mass", "unloaded truck mass"],
        "braking_force": ["braking force", "brake force", "friction force"],
        "launch_speed": ["launch speed", "initial speed", "initial velocity"],
        "launch_angle": ["launch angle", "angle of projection", "angle"],
        "gravity": ["gravity", "gravitational acceleration", "g"],
    }.items():
        units = None
        if key in {"loaded_mass", "unloaded_mass"}:
            units = ["kg", "kilograms", "kilogram"]
        elif key == "braking_force":
            units = ["N", "newtons", "newton"]
        elif key == "launch_speed":
            units = ["m/s", "m s-1", "metres per second", "meters per second"]
        elif key == "launch_angle":
            units = ["degrees", "degree", "deg", "°"]
        value = _question_number(text, labels, units)
        if value is not None:
            params[key] = value

    speed_match = re.search(r"(\d+(?:\.\d+)?)\s*(km/h|kmph|kph|m/s)\b", text, flags=re.IGNORECASE)
    if speed_match:
        speed = float(speed_match.group(1))
        if speed_match.group(2).lower() == "m/s":
            speed *= 3.6
        params["speed_kmh"] = speed

    k_match = re.search(r"(?:k\s*=|k\s+of|into)\s*(\d+)\s*(?:clusters?)?", text, flags=re.IGNORECASE)
    if k_match:
        params["cluster_count"] = float(k_match.group(1))

    return params


def _add_accuracy_metadata(
    visual: Dict[str, Any],
    method: str,
    equations: List[str],
    assumptions: Optional[List[str]] = None,
) -> Dict[str, Any]:
    visual["generation_method"] = method
    visual["source_equations"] = equations
    visual["assumptions"] = assumptions or []
    visual["accuracy_status"] = "verified" if not visual["assumptions"] else "verified_with_assumptions"
    return visual

SIMULATION_TOOL = {
    "type": "function",
    "function": {
        "name": "render_interactive_visualization",
        "description": "Render a question-specific interactive visual model for any STEM concept (Machine Learning, Physics, Mathematics, Data Science, Chemistry).",
        "parameters": {
            "type": "object",
            "properties": {
                "engine": {
                    "type": "string",
                    "enum": ["plotly", "physical_simulation"],
                    "description": "Use 'plotly' for ML, mathematics, kinematics, and projectile trajectories. Use 'physical_simulation' only for vehicle braking/skid."
                },
                "title": {
                    "type": "string",
                    "description": "Descriptive, engaging title for the visual model."
                },
                "plotly_spec": {
                    "type": "object",
                    "description": "Plotly data and layout specification when engine is 'plotly'.",
                    "properties": {
                        "data": {
                            "type": "array",
                            "description": "Array of Plotly traces (scatter, line, bar, etc.)",
                            "items": {"type": "object"}
                        },
                        "layout": {
                            "type": "object",
                            "description": "Plotly layout config (titles, axes, dark theme styling, annotations, shapes)."
                        }
                    }
                },
                "simulation_spec": {
                    "type": "object",
                    "description": "Simulation parameters when engine is 'physical_simulation'.",
                    "properties": {
                        "sim_type": {"type": "string", "enum": ["momentum_stopping"]},
                        "parameters": {"type": "object"}
                    }
                },
                "metrics": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "value": {"type": "string"}
                        },
                        "required": ["label", "value"]
                    },
                    "description": "2-3 primary analytical or numerical metrics to display in badge cards."
                },
                "interactive_controls": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "param": {"type": "string"},
                            "label": {"type": "string"},
                            "min": {"type": "number"},
                            "max": {"type": "number"},
                            "step": {"type": "number"},
                            "default": {"type": "number"},
                            "unit": {"type": "string"},
                            "target_type": {
                                "type": "string",
                                "enum": ["threshold_y", "scale_y", "shift_x", "cluster_spread", "point_marker"],
                                "description": "How the slider modifies the visualization: 'threshold_y' for moving threshold line & finding cutoff x*, 'scale_y' for scaling curve/rate/amplitude, 'cluster_spread' for adjusting cluster variance, 'point_marker' for scrubbing a point."
                            }
                        },
                        "required": ["param", "label", "min", "max", "default"]
                    },
                    "description": "Interactive sliders for the student to manipulate key variables in real time."
                },
                "explanation": {
                    "type": "string",
                    "description": "Socratic explanation of the governing mechanism and what the student should observe. DO NOT reveal the direct test option letter."
                },
                "source_equations": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Equations used to compute or justify the visual. Use an empty array for a non-numeric conceptual visual."
                },
                "assumptions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Every illustrative value or simplifying assumption not explicitly stated in the question."
                }
            },
            "required": ["engine", "title", "explanation"]
        }
    }
}

SYSTEM_PROMPT = """You are Sage, InSync's universal STEM interactive visualization engine.
Your goal is to visualize the QUESTION SCENARIO as an interactive, exploratory sandbox—NEVER visualize the answer or spoil the test question.

CORE PHILOSOPHY: VISUALIZE THE PROBLEM SCENARIO, NOT THE ANSWER KEY
- The student is looking at this question BEFORE answering it.
- Model the system, phenomenon, or mathematical function described in the problem (e.g. the profit curve, the particle trajectory, the dataset distribution, the circuit).
- Let the student interact with parameters (sliders, probe points) to observe how the system behaves.
- STRICT PROHIBITIONS:
  * NEVER write the answer option or conclusion in annotations, titles, or badges (e.g. NEVER write "Local Maximum condition: f'(a)=0 and f''(a)<0", NEVER write "Correct choice: Option A", NEVER state the multiple-choice conclusion).
  * Instead, label the measurable physical/mathematical state: e.g., "Current Tangent Slope f'(x)", "Curvature Concavity", "Path Distance", "Stopping Distance".
  * Let the student make the connection between the visual behavior and the multiple-choice options on their own.

ENGINES:
1. 'plotly' (RECOMMENDED for 90% of STEM):
   - Machine Learning & Optimization:
     * Gradient Descent & Cost Minimization:
       When a question mentions gradient descent, cost function, or loss function (even in the context of Logistic Regression), ALWAYS plot the convex Cost/Loss function bowl J(w) vs Weight w. Do NOT plot a sigmoid probability curve.
       Provide an interactive slider (param: 'step', label: 'Descent Iteration (Step)', target_type: 'gradient_step') showing the parameter step downhill toward the minimum.
       Display live telemetry for Current Cost J(w), Gradient Slope dJ/dw.
     * Logistic Regression / Classification Threshold:
       Only when the question is specifically about classification thresholds, probability mapping, or predicting classes: Plot the sigmoid probability curve with data points and an adjustable threshold line.
     * Clustering: 2D feature scatter space with centroids. Telemetry: Cluster count K, Variance.
   - Calculus & Dynamic Systems:
     * Plot a continuous, smooth function with peaks and valleys (e.g. Profit function or cubic/quadratic with crest and trough).
     * Provide an interactive probe slider (param: 'x_probe', label: 'Probe Position (x)', target_type: 'tangent_probe') that moves a tangent line along the curve.
     * Display live telemetry for slope f'(x) and curvature.
     * DO NOT label the apex as "The Answer". Label it neutrally as "Crest (Peak)" or "Probe Point".
   - Kinematics & Physics:
     * Distance vs. Displacement: 2D trail map with winding scalar path and straight displacement arrow.
     * Average Speed: Distance-Time uniform/non-uniform rate graph with distance and time sliders.
   - Layout MUST use dark aesthetic: paper_bgcolor: "#020617", plot_bgcolor: "#0f172a", font color: "#94a3b8".

2. 'physical_simulation' (Mechanical Motion):
   - Vehicle momentum & braking only (sim_type: "momentum_stopping")
   - Projectile motion must use Plotly so its equation-derived trajectory renders consistently.
"""


def compute_momentum_physics(loaded_mass: float, unloaded_mass: float = 3000.0, speed_kmh: float = 60.0, braking_force: float = 8000.0) -> Dict[str, Any]:
    """Computes momentum, stopping distance, and braking effort for a loaded vs unloaded truck."""
    loaded_mass = max(1000.0, min(float(loaded_mass), 20000.0))
    unloaded_mass = max(500.0, min(float(unloaded_mass), loaded_mass))
    speed_kmh = max(10.0, min(float(speed_kmh), 140.0))
    braking_force = max(1000.0, min(float(braking_force), 30000.0))

    v = speed_kmh / 3.6
    p_loaded = round(loaded_mass * v, 1)
    p_unloaded = round(unloaded_mass * v, 1)
    d_loaded = round((loaded_mass * v * v) / (2 * braking_force), 1)
    d_unloaded = round((unloaded_mass * v * v) / (2 * braking_force), 1)
    ratio = round(loaded_mass / unloaded_mass, 1)

    return {
        "metrics": [
            {"label": "Loaded Momentum (p=mv)", "value": f"{p_loaded:,.0f} kg·m/s"},
            {"label": "Stopping Distance", "value": f"{d_loaded} m ({ratio}x farther)"},
            {"label": "Braking Effort", "value": f"{ratio}x more force required"}
        ],
        "values": {
            "loaded_mass": loaded_mass,
            "unloaded_mass": unloaded_mass,
            "speed_kmh": speed_kmh,
            "braking_force": braking_force,
            "d_loaded": d_loaded,
            "d_unloaded": d_unloaded,
            "p_loaded": p_loaded,
            "p_unloaded": p_unloaded,
            "ratio": ratio
        }
    }


def create_logistic_regression_spec(w: float = 1.5, b: float = -4.5, threshold: float = 0.5) -> Dict[str, Any]:
    """Generates a Plotly interactive specification for Logistic Regression."""
    w = max(-20.0, min(float(w), 20.0))
    if abs(w) < 0.05:
        w = 0.05
    b = max(-100.0, min(float(b), 100.0))
    threshold = max(0.01, min(float(threshold), 0.99))
    # Calculate boundary cutoff x* where sigma(x) = threshold
    # threshold = 1 / (1 + e^{-(wx + b)}) => wx + b = ln(tau / (1 - tau))
    tau = max(0.01, min(threshold, 0.99))
    x_cutoff = round((math.log(tau / (1.0 - tau)) - b) / w, 2)
    x_min = x_cutoff - 4.0
    x_max = x_cutoff + 4.0
    xs = [round(x_min + i * 0.1, 2) for i in range(81)]
    sigmoids = [round(1.0 / (1.0 + math.exp(-(w * x + b))), 3) for x in xs]
    left_samples = [round(x_cutoff - offset, 2) for offset in (3.0, 2.2, 1.4, 0.6)]
    right_samples = [round(x_cutoff + offset, 2) for offset in (0.6, 1.4, 2.2, 3.0)]
    class_zero_x, class_one_x = (left_samples, right_samples) if w > 0 else (right_samples, left_samples)

    data = [
        {
            "x": class_zero_x,
            "y": [0, 0, 0, 0],
            "mode": "markers",
            "name": "Class 0 (Negative)",
            "marker": {"color": "#ef4444", "size": 10, "symbol": "circle"}
        },
        {
            "x": class_one_x,
            "y": [1, 1, 1, 1],
            "mode": "markers",
            "name": "Class 1 (Positive)",
            "marker": {"color": "#10b981", "size": 10, "symbol": "circle"}
        },
        {
            "x": xs,
            "y": sigmoids,
            "mode": "lines",
            "name": "P(y=1) Sigmoid",
            "line": {"color": "#6366f1", "width": 3}
        }
    ]

    layout = {
        "title": {"text": "Logistic Regression: Probability Sigmoid & Decision Threshold", "font": {"color": "#f8fafc", "size": 14}},
        "paper_bgcolor": "#020617",
        "plot_bgcolor": "#0f172a",
        "font": {"color": "#94a3b8", "family": "Inter, sans-serif"},
        "margin": {"l": 50, "r": 30, "t": 40, "b": 40},
        "xaxis": {"title": "Input Feature Value (x)", "range": [x_min, x_max], "gridcolor": "rgba(255,255,255,0.08)", "zerolinecolor": "rgba(255,255,255,0.2)"},
        "yaxis": {"title": "Predicted Probability P(y=1)", "range": [-0.15, 1.15], "gridcolor": "rgba(255,255,255,0.08)", "zerolinecolor": "rgba(255,255,255,0.2)"},
        "shapes": [
            # Horizontal threshold line
            {"type": "line", "x0": x_min, "x1": x_max, "y0": threshold, "y1": threshold, "line": {"dash": "dash", "color": "#f59e0b", "width": 2}},
            # Vertical decision cutoff
            {"type": "line", "x0": x_cutoff, "x1": x_cutoff, "y0": 0, "y1": 1, "line": {"dash": "dot", "color": "#38bdf8", "width": 2}}
        ],
        "annotations": [
            {"x": x_max - 0.8, "y": threshold + 0.07, "text": f"Threshold τ = {threshold}", "showarrow": False, "font": {"color": "#f59e0b", "size": 11}},
            {"x": x_cutoff, "y": 0.05, "text": f"Decision Cutoff x* = {x_cutoff}", "showarrow": True, "arrowhead": 2, "font": {"color": "#38bdf8", "size": 10}}
        ]
    }

    return {
        "engine": "plotly",
        "title": "Logistic Regression: Sigmoid Curve & Decision Threshold",
        "plotly_spec": {"data": data, "layout": layout},
        "metrics": [
            {"label": "Decision Cutoff (x*)", "value": f"x ≥ {x_cutoff}"},
            {"label": "Threshold (τ)", "value": f"{threshold}"},
            {"label": "Model Behavior", "value": "Strict" if threshold > 0.6 else ("Permissive" if threshold < 0.4 else "Balanced")}
        ],
        "interactive_controls": [
            {"param": "threshold", "label": "Decision Threshold (τ)", "min": 0.1, "max": 0.9, "step": 0.05, "default": threshold, "unit": "", "target_type": "threshold_y"}
        ],
        "explanation": "In Logistic Regression, points with P(y=1) ≥ τ are predicted as positive. Raising the threshold shifts the decision cutoff to the right, requiring stronger evidence to predict Class 1, thereby reducing false positives while increasing false negatives."
    }


def create_gradient_descent_spec(w_opt: float = 2.0, lr: float = 0.2, initial_step: int = 0) -> Dict[str, Any]:
    """
    Generates a mathematically exact Plotly interactive sandbox for Gradient Descent & Cost Function Minimization.
    Plots convex Cost bowl J(w) = 0.4 * (w - w_opt)^2 + 0.3 vs Weight (w).
    Trace 1: Cost curve J(w).
    Trace 2: Gradient descent iteration path.
    Trace 3: Active parameter point (w_t, J(w_t)).
    """
    w_opt = max(-20.0, min(float(w_opt), 20.0))
    lr = max(0.001, min(float(lr), 1.0))
    ws = [round(-1.5 + i * 0.15, 2) for i in range(51)]
    cost_ys = [round(0.4 * ((w - w_opt) ** 2) + 0.3, 3) for w in ws]

    # Precompute 6 descent steps starting from w_0 = -1.0
    w_curr = -1.0
    steps_w = [w_curr]
    steps_j = [round(0.4 * ((w_curr - w_opt) ** 2) + 0.3, 3)]
    for _ in range(6):
        grad = 0.8 * (w_curr - w_opt)
        w_curr = round(w_curr - lr * grad, 3)
        steps_w.append(w_curr)
        steps_j.append(round(0.4 * ((w_curr - w_opt) ** 2) + 0.3, 3))

    step = max(0, min(initial_step, len(steps_w) - 1))
    w_active = steps_w[step]
    j_active = steps_j[step]
    grad_active = round(0.8 * (w_active - w_opt), 3)

    data = [
        {
            "x": ws,
            "y": cost_ys,
            "mode": "lines",
            "name": "Cost Function J(w)",
            "line": {"color": "#6366f1", "width": 3, "shape": "spline", "smoothing": 1.3}
        },
        {
            "x": steps_w[:step + 1],
            "y": steps_j[:step + 1],
            "mode": "lines+markers",
            "name": "Descent Path",
            "line": {"color": "#f59e0b", "width": 2, "dash": "dot"},
            "marker": {"color": "#f59e0b", "size": 6}
        },
        {
            "x": [w_active],
            "y": [j_active],
            "mode": "markers",
            "name": "Current Weight (w_t)",
            "marker": {"color": "#10b981", "size": 14, "symbol": "circle", "line": {"color": "#ffffff", "width": 2}}
        }
    ]

    layout = {
        "title": {"text": "Gradient Descent: Cost Function J(w) Minimization", "font": {"color": "#f8fafc", "size": 14}},
        "paper_bgcolor": "#020617",
        "plot_bgcolor": "#0f172a",
        "font": {"color": "#94a3b8", "family": "Inter, sans-serif"},
        "margin": {"l": 55, "r": 30, "t": 40, "b": 40},
        "xaxis": {"title": "Model Weight / Parameter (w)", "gridcolor": "rgba(255,255,255,0.08)", "range": [-2.0, 6.0]},
        "yaxis": {"title": "Cost / Loss J(w)", "gridcolor": "rgba(255,255,255,0.08)", "range": [0.0, 5.5]},
        "annotations": [
            {
                "x": w_opt,
                "y": 0.3,
                "text": "Global Minimum (Lowest Cost)",
                "showarrow": True,
                "arrowhead": 2,
                "arrowcolor": "#38bdf8",
                "font": {"color": "#38bdf8", "size": 11},
                "yshift": -12
            },
            {
                "x": w_active,
                "y": j_active,
                "text": f"Step {step}: J(w) = {j_active:.3f}",
                "showarrow": True,
                "arrowhead": 2,
                "arrowcolor": "#10b981",
                "font": {"color": "#10b981", "size": 10},
                "yshift": 12
            }
        ]
    }

    return {
        "engine": "plotly",
        "archetype": "gradient_descent",
        "title": "Gradient Descent: Minimizing the Cost Function",
        "plotly_spec": {"data": data, "layout": layout},
        "metrics": [
            {"label": "Current Cost J(w)", "value": f"{j_active:.3f}"},
            {"label": "Gradient Slope dJ/dw", "value": f"{grad_active:+.2f}"},
            {"label": "Optimization Status", "value": "Converged at Minimum" if abs(w_active - w_opt) < 0.1 else "Descending Downhill"}
        ],
        "interactive_controls": [
            {"param": "step", "label": "Descent Iteration (Step)", "min": 0, "max": 6, "step": 1, "default": step, "unit": "", "target_type": "gradient_step"}
        ],
        "explanation": "In machine learning models like logistic regression, gradient descent iteratively computes the gradient (slope) of the cost function with respect to weights and takes steps in the opposite direction. Notice how the cost J(w) decreases with each step until it reaches the global minimum."
    }


def create_projectile_motion_spec(
    launch_speed: float = 25.0,
    launch_angle: float = 45.0,
    gravity: float = 9.81,
) -> Dict[str, Any]:
    """Create an equation-derived projectile trajectory without invented plot points."""
    launch_speed = max(1.0, min(float(launch_speed), 250.0))
    launch_angle = max(1.0, min(float(launch_angle), 89.0))
    gravity = max(1.0, min(float(gravity), 30.0))
    theta = math.radians(launch_angle)
    flight_time = (2.0 * launch_speed * math.sin(theta)) / gravity
    horizontal_range = launch_speed * math.cos(theta) * flight_time
    max_height = (launch_speed ** 2) * (math.sin(theta) ** 2) / (2.0 * gravity)
    times = [flight_time * i / 60.0 for i in range(61)]
    xs = [round(launch_speed * math.cos(theta) * t, 3) for t in times]
    ys = [round(max(0.0, launch_speed * math.sin(theta) * t - 0.5 * gravity * t * t), 3) for t in times]

    return {
        "engine": "plotly",
        "archetype": "projectile_motion",
        "title": "Projectile Motion: Equation-Derived Trajectory",
        "plotly_spec": {
            "data": [{
                "x": xs,
                "y": ys,
                "mode": "lines",
                "name": "Projectile path",
                "line": {"color": "#818cf8", "width": 4},
            }],
            "layout": {
                "title": {"text": "Projectile trajectory (without air resistance)", "font": {"color": "#f8fafc", "size": 14}},
                "paper_bgcolor": "#020617",
                "plot_bgcolor": "#0f172a",
                "font": {"color": "#94a3b8", "family": "Inter, sans-serif"},
                "margin": {"l": 55, "r": 25, "t": 40, "b": 45},
                "xaxis": {"title": "Horizontal distance (m)", "gridcolor": "rgba(255,255,255,0.08)", "rangemode": "tozero"},
                "yaxis": {"title": "Height (m)", "gridcolor": "rgba(255,255,255,0.08)", "rangemode": "tozero"},
                "annotations": [{
                    "x": round(horizontal_range / 2.0, 3),
                    "y": round(max_height, 3),
                    "text": f"Peak height: {max_height:.2f} m",
                    "showarrow": True,
                    "arrowhead": 2,
                    "font": {"color": "#f59e0b", "size": 11},
                }],
            },
        },
        "metrics": [
            {"label": "Flight time", "value": f"{flight_time:.2f} s"},
            {"label": "Horizontal range", "value": f"{horizontal_range:.2f} m"},
            {"label": "Maximum height", "value": f"{max_height:.2f} m"},
        ],
        "interactive_controls": [],
        "explanation": (
            "The plotted points are calculated from x(t)=v₀cos(θ)t and "
            "y(t)=v₀sin(θ)t−½gt². Air resistance is excluded unless the question states otherwise."
        ),
    }


def create_linear_regression_spec(slope: float = 1.5, intercept: float = 1.0) -> Dict[str, Any]:
    """Create a deterministic regression visual from y = mx + b."""
    slope = max(-50.0, min(float(slope), 50.0))
    intercept = max(-100.0, min(float(intercept), 100.0))
    xs = [round(-5.0 + i * 0.25, 2) for i in range(41)]
    line_ys = [round(slope * x + intercept, 3) for x in xs]
    sample_x = [-4, -3, -2, -1, 0, 1, 2, 3, 4]
    residual_offsets = [0.8, -0.6, 0.35, -0.45, 0.25, 0.55, -0.4, 0.7, -0.5]
    sample_y = [round(slope * x + intercept + residual_offsets[i], 3) for i, x in enumerate(sample_x)]

    return {
        "engine": "plotly",
        "archetype": "linear_regression",
        "title": "Linear Regression: Prediction Line and Residuals",
        "plotly_spec": {
            "data": [
                {"x": sample_x, "y": sample_y, "mode": "markers", "name": "Observed samples", "marker": {"color": "#38bdf8", "size": 10}},
                {"x": xs, "y": line_ys, "mode": "lines", "name": f"ŷ = {slope:g}x + {intercept:g}", "line": {"color": "#f59e0b", "width": 3}},
            ],
            "layout": {
                "title": {"text": "Observed values and fitted linear model", "font": {"color": "#f8fafc", "size": 14}},
                "paper_bgcolor": "#020617",
                "plot_bgcolor": "#0f172a",
                "font": {"color": "#94a3b8", "family": "Inter, sans-serif"},
                "margin": {"l": 50, "r": 25, "t": 40, "b": 45},
                "xaxis": {"title": "Feature x", "gridcolor": "rgba(255,255,255,0.08)"},
                "yaxis": {"title": "Target y", "gridcolor": "rgba(255,255,255,0.08)"},
            },
        },
        "metrics": [
            {"label": "Slope (m)", "value": f"{slope:g}"},
            {"label": "Intercept (b)", "value": f"{intercept:g}"},
            {"label": "Model equation", "value": f"ŷ = {slope:g}x + {intercept:g}"},
        ],
        "interactive_controls": [],
        "explanation": (
            "The line shows predictions from ŷ=mx+b. Vertical differences between the sample points "
            "and the line are residuals; least squares minimizes their squared values."
        ),
    }


def create_kmeans_clustering_spec(k: int = 3) -> Dict[str, Any]:
    """Generates a Plotly interactive specification for K-Means Clustering."""
    k = max(2, min(int(k), 6))
    centers = [(1.7, 2.0), (5.7, 5.9), (8.3, 2.2), (3.1, 7.8), (8.2, 7.9), (5.0, 1.0)][:k]
    colors = ["#3b82f6", "#10b981", "#f59e0b", "#a855f7", "#f43f5e", "#06b6d4"]
    offsets = [(-.5, -.4), (.1, .2), (-.2, -.1), (.5, .5), (.2, 0)]
    data = []
    for index, (center_x, center_y) in enumerate(centers):
        data.append({
            "x": [round(center_x + dx, 2) for dx, _ in offsets],
            "y": [round(center_y + dy, 2) for _, dy in offsets],
            "mode": "markers",
            "name": f"Cluster {index + 1}",
            "marker": {"color": colors[index], "size": 10},
        })
    data.append({
        "x": [center[0] for center in centers],
        "y": [center[1] for center in centers],
        "mode": "markers",
        "name": "Centroids (μ)",
        "marker": {"color": "#ec4899", "size": 16, "symbol": "star"},
    })

    layout = {
        "title": {"text": f"K-Means Clustering Space (K = {k})", "font": {"color": "#f8fafc", "size": 14}},
        "paper_bgcolor": "#020617",
        "plot_bgcolor": "#0f172a",
        "font": {"color": "#94a3b8", "family": "Inter, sans-serif"},
        "margin": {"l": 50, "r": 30, "t": 40, "b": 40},
        "xaxis": {"title": "Feature 1 (X₁)", "gridcolor": "rgba(255,255,255,0.08)"},
        "yaxis": {"title": "Feature 2 (X₂)", "gridcolor": "rgba(255,255,255,0.08)"}
    }

    return {
        "engine": "plotly",
        "title": f"K-Means Clustering Dynamics (K = {k})",
        "plotly_spec": {"data": data, "layout": layout},
        "metrics": [
            {"label": "Number of Clusters (K)", "value": str(k)},
            {"label": "Distance Metric", "value": "Euclidean (L2)"},
            {"label": "Convergence", "value": "Centroids Stable"}
        ],
        "interactive_controls": [
            {"param": "spread", "label": "Cluster Spread Variance", "min": 0.5, "max": 2.0, "step": 0.1, "default": 1.0, "unit": "x", "target_type": "cluster_spread"}
        ],
        "explanation": "K-Means alternates between two steps: assigning every data point to its nearest centroid using Euclidean distance, and updating each centroid to the geometric mean of its assigned points."
    }


def create_kinematics_concept_spec(question_text: str) -> Dict[str, Any]:
    """Generates a Plotly specification for Kinematics concepts (Distance vs Displacement, Average Speed)."""
    q_lower = question_text.lower()

    # Distance vs. Displacement
    if "displacement" in q_lower or "scalar" in q_lower or "vector" in q_lower:
        data = [
            # Winding actual path (Distance)
            {
                "x": [0, 2, 4, 3, 6, 8, 10],
                "y": [0, 4, 2, 6, 5, 8, 6],
                "mode": "lines+markers",
                "name": "Actual Path Traveled (Distance = 24.5 m)",
                "line": {"color": "#f59e0b", "width": 3, "dash": "dot"},
                "marker": {"size": 6}
            },
            # Direct straight displacement vector
            {
                "x": [0, 10],
                "y": [0, 6],
                "mode": "lines+markers",
                "name": "Net Displacement Vector (11.7 m @ 31°)",
                "line": {"color": "#38bdf8", "width": 4},
                "marker": {"size": 10, "color": "#38bdf8"}
            }
        ]

        layout = {
            "title": {"text": "Distance (Scalar Path) vs. Displacement (Vector Arrow)", "font": {"color": "#f8fafc", "size": 14}},
            "paper_bgcolor": "#020617",
            "plot_bgcolor": "#0f172a",
            "font": {"color": "#94a3b8", "family": "Inter, sans-serif"},
            "margin": {"l": 50, "r": 30, "t": 40, "b": 40},
            "xaxis": {"title": "East-West Position (m)", "gridcolor": "rgba(255,255,255,0.08)"},
            "yaxis": {"title": "North-South Position (m)", "gridcolor": "rgba(255,255,255,0.08)"},
            "annotations": [
                {"x": 0, "y": 0, "text": "Start (A)", "showarrow": True, "arrowhead": 2, "font": {"color": "#10b981", "size": 11}},
                {"x": 10, "y": 6, "text": "Finish (B)", "showarrow": True, "arrowhead": 2, "font": {"color": "#f43f5e", "size": 11}}
            ]
        }

        return {
            "engine": "plotly",
            "title": "Distance (Scalar) vs. Displacement (Vector)",
            "plotly_spec": {"data": data, "layout": layout},
            "metrics": [
                {"label": "Path Distance (Scalar)", "value": "24.5 m"},
                {"label": "Displacement (Vector)", "value": "11.7 m @ 31°"},
                {"label": "Key Property", "value": "Distance ≥ Displacement"}
            ],
            "interactive_controls": [
                {"param": "detour", "label": "Path Detour / Winding Factor", "min": 1.0, "max": 3.0, "step": 0.2, "default": 1.8, "unit": "x", "target_type": "detour"}
            ],
            "explanation": "Distance is a scalar quantity measuring the total path length traveled regardless of direction. Displacement is a vector quantity pointing directly in a straight line from the initial position to the final position."
        }

    # Average Speed & Rate of Motion (v = d/t)
    times = [0, 2, 4, 6, 8, 10]
    speeds = [0, 30, 60, 90, 120, 150]
    data = [
        {
            "x": times,
            "y": speeds,
            "mode": "lines+markers",
            "name": "Uniform Motion (v = 15 m/s)",
            "line": {"color": "#10b981", "width": 3},
            "marker": {"size": 8, "color": "#34d399"}
        }
    ]

    layout = {
        "title": {"text": "Average Speed: Slope of Distance vs. Time (v = Δd / Δt)", "font": {"color": "#f8fafc", "size": 14}},
        "paper_bgcolor": "#020617",
        "plot_bgcolor": "#0f172a",
        "font": {"color": "#94a3b8", "family": "Inter, sans-serif"},
        "margin": {"l": 50, "r": 30, "t": 40, "b": 40},
        "xaxis": {"title": "Elapsed Time (seconds)", "gridcolor": "rgba(255,255,255,0.08)"},
        "yaxis": {"title": "Total Distance Traveled (meters)", "gridcolor": "rgba(255,255,255,0.08)"},
        "annotations": [
            {"x": 6, "y": 90, "text": "Slope = v = 150m / 10s = 15 m/s", "showarrow": True, "arrowhead": 2, "font": {"color": "#10b981", "size": 11}}
        ]
    }

    return {
        "engine": "plotly",
        "title": "Formula for Average Speed: v = Total Distance / Total Time",
        "plotly_spec": {"data": data, "layout": layout},
        "metrics": [
            {"label": "Total Distance", "value": "150 m"},
            {"label": "Total Time", "value": "10 s"},
            {"label": "Average Speed (v)", "value": "15 m/s (54 km/h)"}
        ],
        "interactive_controls": [
            {"param": "speed", "label": "Average Speed / Slope (v)", "min": 5, "max": 40, "step": 2, "default": 15, "unit": " m/s", "target_type": "scale_y"}
        ],
        "explanation": "Average speed is defined as the total distance covered divided by the total time taken (v = d / t). On a distance-time graph, the steepness (slope) of the line directly represents the speed."
    }


def create_generic_stem_spec(question_text: str = "") -> Dict[str, Any]:
    """Generates a universal scientific rate/functional model for any general STEM question."""
    xs = [round(i * 0.5, 1) for i in range(0, 21)]
    ys = [round(2.0 * x, 1) for x in xs]

    data = [
        {
            "x": xs,
            "y": ys,
            "mode": "lines+markers",
            "name": "Dependent Response f(x)",
            "line": {"color": "#6366f1", "width": 3},
            "marker": {"size": 6, "color": "#818cf8"}
        }
    ]

    layout = {
        "title": {"text": "Scientific Parameter Response Curve", "font": {"color": "#f8fafc", "size": 14}},
        "paper_bgcolor": "#020617",
        "plot_bgcolor": "#0f172a",
        "font": {"color": "#94a3b8", "family": "Inter, sans-serif"},
        "margin": {"l": 50, "r": 30, "t": 40, "b": 40},
        "xaxis": {"title": "Independent Variable (X)", "gridcolor": "rgba(255,255,255,0.08)"},
        "yaxis": {"title": "System Response (Y)", "gridcolor": "rgba(255,255,255,0.08)"},
        "annotations": [
            {"x": 5.0, "y": 10.0, "text": "Rate Slope k = 2.0", "showarrow": True, "arrowhead": 2, "font": {"color": "#6366f1", "size": 11}}
        ]
    }

    return {
        "engine": "plotly",
        "title": "Universal Scientific Relationship Model",
        "plotly_spec": {"data": data, "layout": layout},
        "metrics": [
            {"label": "Governing Law", "value": "Direct Proportionality"},
            {"label": "Rate Constant (k)", "value": "2.0"},
            {"label": "System State", "value": "Linear Dynamic"}
        ],
        "interactive_controls": [
            {"param": "rate", "label": "Rate Constant (k)", "min": 0.5, "max": 5.0, "step": 0.25, "default": 2.0, "unit": "", "target_type": "scale_y"}
        ],
        "explanation": "Many foundational STEM systems exhibit proportional scaling relationships where adjusting the governing rate constant directly modulates the output response across the parameter domain."
    }


def create_calculus_extrema_spec(is_max: bool = True) -> Dict[str, Any]:
    """Generates a smooth, interactive Plotly sandbox to explore function slopes and curvature without spoiling answers."""
    xs = [round(-3.0 + i * 0.25, 2) for i in range(25)]
    a = -0.5 if is_max else 0.5
    c = 3.0 if is_max else 0.5
    ys = [round(a * (x ** 2) + c, 3) for x in xs]

    # Tangent at critical probe point x=0 (f'(0) = 0, horizontal line)
    tangent_xs = [-1.5, 0.0, 1.5]
    tangent_ys = [c, c, c]

    data = [
        {
            "x": xs,
            "y": ys,
            "mode": "lines",
            "name": "Function Curve f(x)",
            "line": {"color": "#6366f1", "width": 3, "shape": "spline", "smoothing": 1.3}
        },
        {
            "x": tangent_xs,
            "y": tangent_ys,
            "mode": "lines",
            "name": "Tangent Line at Probe",
            "line": {"color": "#f59e0b", "width": 2, "dash": "dash"}
        },
        {
            "x": [0.0],
            "y": [c],
            "mode": "markers",
            "name": "Probe Point (x₀)",
            "marker": {"color": "#10b981", "size": 12, "symbol": "circle"}
        }
    ]

    kind = "Crest (Peak)" if is_max else "Trough (Valley)"
    concavity_desc = "Concave Down (Bends Downward)" if is_max else "Concave Up (Bends Upward)"

    layout = {
        "title": {"text": "Function Behavior & Tangent Slope Explorer", "font": {"color": "#f8fafc", "size": 14}},
        "paper_bgcolor": "#020617",
        "plot_bgcolor": "#0f172a",
        "font": {"color": "#94a3b8", "family": "Inter, sans-serif"},
        "margin": {"l": 50, "r": 30, "t": 40, "b": 40},
        "xaxis": {"title": "Input Position (x)", "gridcolor": "rgba(255,255,255,0.08)", "range": [-3.5, 3.5]},
        "yaxis": {"title": "Function Value f(x)", "gridcolor": "rgba(255,255,255,0.08)", "range": [-2.0, 4.0]},
        "annotations": [
            {
                "x": 0.0,
                "y": c,
                "text": f"{kind}: Tangent Slope f'(x) = 0.0",
                "showarrow": True,
                "arrowhead": 2,
                "arrowcolor": "#10b981",
                "font": {"color": "#10b981", "size": 11},
                "yshift": 10
            }
        ]
    }

    return {
        "engine": "plotly",
        "title": "Function Curve & Tangent Slope Exploration",
        "plotly_spec": {"data": data, "layout": layout},
        "metrics": [
            {"label": "Probe Position (x)", "value": "0.0"},
            {"label": "Tangent Slope f'(x)", "value": "0.0 (Horizontal Tangent)"},
            {"label": "Curvature / Concavity", "value": concavity_desc}
        ],
        "interactive_controls": [
            {"param": "x_probe", "label": "Probe Position (x)", "min": -2.0, "max": 2.0, "step": 0.5, "default": 0.0, "unit": "", "target_type": "tangent_probe"}
        ],
        "explanation": "Scrub the probe point along the curve to observe how the tangent slope f'(x) changes across rising slopes, crests (peaks), and falling slopes. Notice what occurs to the tangent line at the peak and how the curve curves."
    }


def _linear_equation_parameters(question_text: str) -> Optional[Dict[str, float]]:
    match = re.search(
        r"(?:y|ŷ|f\(x\))\s*=\s*([+-]?(?:\d+(?:\.\d+)?|\.\d+)?)\s*\*?\s*x\s*([+-]\s*\d+(?:\.\d+)?)?",
        question_text.replace("−", "-"),
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    raw_slope = (match.group(1) or "1").replace(" ", "")
    if raw_slope in {"+", ""}:
        raw_slope = "1"
    elif raw_slope == "-":
        raw_slope = "-1"
    try:
        slope = float(raw_slope)
        intercept = float((match.group(2) or "0").replace(" ", ""))
    except ValueError:
        return None
    return {"slope": slope, "intercept": intercept}


def create_verified_simulation(question_text: str) -> Optional[Dict[str, Any]]:
    """Use deterministic maths for supported concepts before consulting an LLM."""
    q_lower = question_text.lower()
    params = _extract_explicit_parameters(question_text)

    if any(term in q_lower for term in ["gradient descent", "cost function", "loss function", "learning rate"]):
        visual = create_gradient_descent_spec()
        return _add_accuracy_metadata(
            visual,
            "deterministic",
            ["J(w)=0.4(w−2)²+0.3", "dJ/dw=0.8(w−2)", "wₜ₊₁=wₜ−η·dJ/dw"],
            ["Illustrative convex cost function used because the question does not supply a complete cost equation."],
        )

    if any(term in q_lower for term in ["logistic regression", "sigmoid", "classification threshold", "probability threshold"]):
        w = params.get("weight", 1.5)
        b = params.get("bias", -4.5)
        threshold = params.get("threshold", 0.5)
        assumptions = []
        if "weight" not in params:
            assumptions.append("Illustrative logistic weight w=1.5.")
        if "bias" not in params:
            assumptions.append("Illustrative logistic bias b=−4.5.")
        if "threshold" not in params:
            assumptions.append("Decision threshold τ=0.5.")
        return _add_accuracy_metadata(
            create_logistic_regression_spec(w=w, b=b, threshold=threshold),
            "deterministic",
            ["P(y=1|x)=1/(1+e^(−(wx+b)))", "x*=(ln(τ/(1−τ))−b)/w"],
            assumptions,
        )

    if any(term in q_lower for term in ["k-means", "kmeans", "centroid"]):
        cluster_count = int(params.get("cluster_count", 3))
        assumptions = [] if "cluster_count" in params else ["Illustrative K=3 feature-space dataset."]
        return _add_accuracy_metadata(
            create_kmeans_clustering_spec(k=cluster_count),
            "deterministic",
            ["μⱼ=(1/|Cⱼ|)Σxᵢ", "assign(xᵢ)=argminⱼ ||xᵢ−μⱼ||₂"],
            assumptions,
        )

    if any(term in q_lower for term in ["projectile", "trajectory", "angle of projection"]):
        launch_speed = params.get("launch_speed", params.get("speed_kmh", 90.0) / 3.6)
        launch_angle = params.get("launch_angle", 45.0)
        gravity = params.get("gravity", 9.81)
        assumptions = []
        if "launch_speed" not in params and "speed_kmh" not in params:
            assumptions.append("Illustrative launch speed v₀=25 m/s.")
        if "launch_angle" not in params:
            assumptions.append("Illustrative launch angle θ=45°.")
        if "gravity" not in params:
            assumptions.append("Earth gravity g=9.81 m/s².")
        assumptions.append("Air resistance is neglected.")
        return _add_accuracy_metadata(
            create_projectile_motion_spec(launch_speed, launch_angle, gravity),
            "deterministic",
            ["x(t)=v₀cos(θ)t", "y(t)=v₀sin(θ)t−½gt²"],
            assumptions,
        )

    if any(term in q_lower for term in ["truck", "momentum", "stopping distance", "braking", "brake force"]):
        loaded_mass = params.get("loaded_mass", 9000.0)
        unloaded_mass = params.get("unloaded_mass", 3000.0)
        speed_kmh = params.get("speed_kmh", 60.0)
        braking_force = params.get("braking_force", 8000.0)
        physics = compute_momentum_physics(loaded_mass, unloaded_mass, speed_kmh, braking_force)
        values = physics["values"]
        assumptions = []
        for key, message in {
            "loaded_mass": "Illustrative loaded mass of 9000 kg.",
            "unloaded_mass": "Illustrative unloaded mass of 3000 kg.",
            "speed_kmh": "Illustrative initial speed of 60 km/h.",
            "braking_force": "Constant illustrative braking force of 8000 N.",
        }.items():
            if key not in params:
                assumptions.append(message)
        visual = {
            "engine": "physical_simulation",
            "archetype": "momentum_stopping",
            "title": "Momentum and Constant-Force Stopping Distance",
            "simulation_spec": {
                "sim_type": "momentum_stopping",
                "parameters": {
                    "loaded_mass": values["loaded_mass"],
                    "unloaded_mass": values["unloaded_mass"],
                    "speed_kmh": values["speed_kmh"],
                    "braking_force": values["braking_force"],
                },
            },
            "metrics": physics["metrics"],
            "interactive_controls": [
                {"param": "loaded_mass", "label": "Loaded mass", "min": 4000, "max": 15000, "step": 500, "default": values["loaded_mass"], "unit": " kg"},
                {"param": "speed_kmh", "label": "Initial speed", "min": 10, "max": 140, "step": 5, "default": values["speed_kmh"], "unit": " km/h"},
                {"param": "braking_force", "label": "Braking force", "min": 1000, "max": 30000, "step": 500, "default": values["braking_force"], "unit": " N"},
            ],
            "explanation": "Momentum is p=mv. Under a constant opposing braking force, the work-energy relation gives stopping distance d=mv²/(2F).",
        }
        return _add_accuracy_metadata(visual, "deterministic", ["p=mv", "d=mv²/(2F)"], assumptions)

    if any(term in q_lower for term in ["linear regression", "least squares", "regression line"]):
        equation = _linear_equation_parameters(question_text)
        assumptions = []
        if equation is None:
            equation = {"slope": 1.5, "intercept": 1.0}
            assumptions.append("Illustrative fitted line ŷ=1.5x+1 and illustrative sample residuals.")
        else:
            assumptions.append("Sample observations around the supplied line are illustrative.")
        return _add_accuracy_metadata(
            create_linear_regression_spec(**equation),
            "deterministic",
            ["ŷ=mx+b", "residualᵢ=yᵢ−ŷᵢ", "SSE=Σ(yᵢ−ŷᵢ)²"],
            assumptions,
        )

    if any(term in q_lower for term in ["maxima", "minima", "local maximum", "local minimum", "concavity", "critical point"]):
        is_max = not ("minimum" in q_lower or "minima" in q_lower)
        return _add_accuracy_metadata(
            create_calculus_extrema_spec(is_max=is_max),
            "deterministic",
            ["f'(x₀)=0 at the illustrated stationary point", "f''(x) determines concavity"],
            ["Illustrative quadratic function used because no complete function is supplied."],
        )

    if any(term in q_lower for term in ["distance vs displacement", "distance and displacement", "net displacement"]):
        return _add_accuracy_metadata(
            create_kinematics_concept_spec("displacement"),
            "deterministic",
            ["displacement=final position−initial position", "distance=total path length"],
            ["Path coordinates are illustrative unless supplied by the question."],
        )

    if any(term in q_lower for term in ["average speed", "distance-time", "distance time graph"]):
        return _add_accuracy_metadata(
            create_kinematics_concept_spec("speed"),
            "deterministic",
            ["average speed=total distance/total time", "speed=gradient of a distance-time graph"],
            ["Illustrative uniform motion of 150 m in 10 s."],
        )

    return None


def validate_and_normalize_simulation(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Validate model output before it is displayed or stored for approval."""
    try:
        visual = json.loads(json.dumps(raw, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise VisualizationValidationError("Visualization is not valid JSON.") from exc

    engine = visual.get("engine")
    if engine not in ALLOWED_ENGINES:
        raise VisualizationValidationError("Unsupported visualization engine.")
    if not isinstance(visual.get("title"), str) or not visual["title"].strip():
        raise VisualizationValidationError("Visualization title is missing.")
    if not isinstance(visual.get("explanation"), str) or not visual["explanation"].strip():
        raise VisualizationValidationError("Visualization explanation is missing.")

    visible_text = " ".join([
        visual.get("title", ""),
        visual.get("explanation", ""),
        json.dumps(visual.get("metrics", [])),
    ]).lower()
    if re.search(r"\bcorrect\s+(?:answer|option)\b|\banswer\s+is\s+(?:option\s+)?[a-d]\b", visible_text):
        raise VisualizationValidationError("Visualization reveals the answer.")

    metrics = visual.get("metrics", [])
    if not isinstance(metrics, list) or len(metrics) > 6:
        raise VisualizationValidationError("Visualization metrics are malformed.")
    visual["metrics"] = [
        {"label": str(metric.get("label", ""))[:100], "value": str(metric.get("value", ""))[:160]}
        for metric in metrics
        if isinstance(metric, dict) and metric.get("label") is not None and metric.get("value") is not None
    ]

    controls = visual.get("interactive_controls", [])
    if not isinstance(controls, list) or len(controls) > 6:
        raise VisualizationValidationError("Visualization controls are malformed.")
    normalized_controls = []
    deterministic = visual.get("generation_method") == "deterministic"
    for control in controls:
        if not isinstance(control, dict):
            raise VisualizationValidationError("Visualization control is malformed.")
        minimum, maximum, default = control.get("min"), control.get("max"), control.get("default")
        if not all(_finite_number(value) for value in [minimum, maximum, default]) or minimum >= maximum:
            raise VisualizationValidationError("Visualization control range is invalid.")
        control["default"] = max(minimum, min(default, maximum))
        target_type = control.get("target_type")
        if not deterministic and target_type not in ALLOWED_CONTROL_TARGETS:
            continue
        if target_type and target_type not in ALLOWED_CONTROL_TARGETS:
            control.pop("target_type", None)
        normalized_controls.append(control)
    visual["interactive_controls"] = normalized_controls

    if engine == "plotly":
        spec = visual.get("plotly_spec")
        if not isinstance(spec, dict):
            raise VisualizationValidationError("Plotly specification is missing.")
        traces = spec.get("data")
        if not isinstance(traces, list) or not 1 <= len(traces) <= MAX_PLOT_TRACES:
            raise VisualizationValidationError("Plotly trace count is invalid.")
        for trace in traces:
            if not isinstance(trace, dict):
                raise VisualizationValidationError("Plotly trace is malformed.")
            x_values, y_values = trace.get("x"), trace.get("y")
            if x_values is not None and (not isinstance(x_values, list) or len(x_values) > MAX_POINTS_PER_TRACE):
                raise VisualizationValidationError("Plotly x-values are invalid.")
            if y_values is not None and (not isinstance(y_values, list) or len(y_values) > MAX_POINTS_PER_TRACE):
                raise VisualizationValidationError("Plotly y-values are invalid.")
            trace_type = str(trace.get("type", "scatter")).lower()
            if (
                trace_type not in {"heatmap", "contour", "surface"}
                and isinstance(x_values, list)
                and isinstance(y_values, list)
                and len(x_values) != len(y_values)
            ):
                raise VisualizationValidationError("Plotly x/y lengths do not match.")
            for values in (x_values, y_values):
                if isinstance(values, list):
                    for value in values:
                        if isinstance(value, (int, float)) and not _finite_number(value):
                            raise VisualizationValidationError("Plotly contains a non-finite value.")
            z_values = trace.get("z")
            if z_values is not None:
                if not isinstance(z_values, list) or len(z_values) > MAX_POINTS_PER_TRACE:
                    raise VisualizationValidationError("Plotly z-values are invalid.")
                for row in z_values:
                    cells = row if isinstance(row, list) else [row]
                    if len(cells) > MAX_POINTS_PER_TRACE:
                        raise VisualizationValidationError("Plotly z-values are too large.")
                    for value in cells:
                        if isinstance(value, (int, float)) and not _finite_number(value):
                            raise VisualizationValidationError("Plotly contains a non-finite value.")
        layout = spec.setdefault("layout", {})
        if not isinstance(layout, dict):
            raise VisualizationValidationError("Plotly layout is malformed.")
        layout.setdefault("paper_bgcolor", "#020617")
        layout.setdefault("plot_bgcolor", "#0f172a")
        layout.setdefault("font", {}).setdefault("color", "#94a3b8")
        layout["font"].setdefault("family", "Inter, sans-serif")
        margin = layout.setdefault("margin", {})
        for side, value in {"l": 45, "r": 25, "t": 35, "b": 35}.items():
            margin.setdefault(side, value)
        for trace in traces:
            if "lines" in str(trace.get("mode", "")):
                line = trace.setdefault("line", {})
                if "shape" not in line and line.get("dash") not in {"dash", "dot"}:
                    line["shape"] = "spline"
                    line["smoothing"] = 1.0
    else:
        spec = visual.get("simulation_spec")
        if not isinstance(spec, dict) or spec.get("sim_type") not in ALLOWED_PHYSICAL_SIMULATIONS:
            raise VisualizationValidationError("Physical simulation specification is invalid.")
        parameters = spec.get("parameters", {})
        if not isinstance(parameters, dict) or any(not _finite_number(value) for value in parameters.values()):
            raise VisualizationValidationError("Physical simulation parameters are invalid.")

    assumptions = visual.get("assumptions", [])
    equations = visual.get("source_equations", [])
    visual["assumptions"] = [str(item)[:240] for item in assumptions[:8]] if isinstance(assumptions, list) else []
    visual["source_equations"] = [str(item)[:240] for item in equations[:8]] if isinstance(equations, list) else []
    visual.setdefault("generation_method", "model_generated")
    visual.setdefault("accuracy_status", "model_validated")
    return visual


def create_fallback_simulation(question_text: str = "") -> Dict[str, Any]:
    """Return a verified offline visual, never a generic graph unrelated to the question."""
    visual = create_verified_simulation(question_text)
    if visual:
        return validate_and_normalize_simulation(visual)
    raise VisualizationValidationError(
        "No verified offline visualization is available for this question."
    )


def is_question_visualizable(question_text: str, concept_tags: Optional[str] = None) -> bool:
    """
    Heuristic classifier determining if a STEM question describes a continuous,
    dynamic, spatial, or mathematical system suitable for interactive visualization,
    or if it is a purely definitional, procedural, or recall question.
    """
    if not question_text:
        return False

    q = question_text.strip().lower()

    # 1. Definitional / Recall anti-patterns that must not have forced graphs
    anti_patterns = [
        r"\bdefinition of\b",
        r"\bdefined as\b",
        r"\bhow is .+ defined\b",
        r"\bwhat does .+ represent\b",
        r"\bwhat does .+ indicate when it equals zero\b",
        r"\bwhat does .+ mean\b",
        r"\bfirst step\b",
        r"\bwhich of the following (defines|is the definition)\b",
        r"\bgiven in the context\b",
        r"\bwho (developed|proposed|discovered)\b",
        r"\bwhat year\b",
        r"\bstate the law\b",
    ]
    for pattern in anti_patterns:
        if re.search(pattern, q):
            return False

    # 2. Visualizable STEM systems & dynamic phenomena
    visual_patterns = [
        r"\b(local maximum|local minimum|maxima|minima)\b",
        r"\b(tangent|slope|concavity|inflection point|critical point)\b",
        r"\bf['’]\(|\bf['’]['’]\(",
        r"\b(derivative|rate of change|integral|area under)\b",
        r"\b(momentum|stopping distance|braking|skid|truck)\b",
        r"\b(projectile|trajectory|displacement|velocity-time|distance-time|acceleration)\b",
        r"\b(logistic regression|linear regression|sigmoid|decision boundary|k-means|centroid|least squares)\b",
        r"\b(demand curve|supply curve|profit curve|cost curve|equilibrium)\b",
    ]
    for pattern in visual_patterns:
        if re.search(pattern, q):
            return True

    return False


class VisualizationService:
    def __init__(self) -> None:
        settings = get_settings()
        self.api_key = settings.OPENAI_API_KEY or os.getenv("OPENAI_API_KEY", "")
        self.model = (
            getattr(settings, "VISUALIZATION_MODEL", "")
            or os.getenv("VISUALIZATION_MODEL", "")
            or getattr(settings, "LLM_MODEL", "gpt-4o-mini")
            or "gpt-4o-mini"
        )

    async def plan_simulation(self, question_text: str, context: Optional[str] = None) -> Dict[str, Any]:
        """Generate a verified deterministic visual or a validated model-generated spec."""
        verified = create_verified_simulation(question_text)
        if verified:
            return validate_and_normalize_simulation(verified)

        if not self.api_key:
            return create_fallback_simulation(question_text)

        user_content = (
            "Create one scientifically defensible visualization for this exact assignment question.\n"
            f"QUESTION (source of truth):\n\"\"\"{question_text}\"\"\"\n"
            "ACCURACY RULES:\n"
            "1. Use every numeric value and unit exactly as stated.\n"
            "2. Do not invent measurements, data points, coefficients, or named categories.\n"
            "3. If an illustrative value is necessary, list it explicitly in assumptions.\n"
            "4. Put every equation used to compute the visual in source_equations.\n"
            "5. Keep x/y arrays the same length and derive numeric points from the stated equation.\n"
            "6. Prefer a non-numeric conceptual diagram over a fabricated quantitative graph.\n"
            "7. Never reveal which answer choice is correct."
        )
        if context:
            user_content += f"\nCOURSE CONTEXT (not permission to invent data): {context}"

        try:
            async with AsyncOpenAI(api_key=self.api_key) as client:
                response = await client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_content}
                    ],
                    tools=[SIMULATION_TOOL],
                    tool_choice={"type": "function", "function": {"name": "render_interactive_visualization"}},
                    parallel_tool_calls=False,
                    temperature=0,
                )

                choice = response.choices[0]
                if choice.message.tool_calls:
                    call = choice.message.tool_calls[0]
                    arguments = json.loads(call.function.arguments)
                    arguments.setdefault(
                        "assumptions",
                        ["Model-generated visual; professor verification is required before approval."],
                    )
                    return validate_and_normalize_simulation(arguments)

                raise VisualizationValidationError("The model did not return a visualization specification.")

        except VisualizationValidationError:
            raise
        except Exception as e:
            logger.exception("Error in plan_simulation: %s", e)
            raise VisualizationValidationError(
                "The visualization could not be generated and verified."
            ) from e


_visualization_service: Optional[VisualizationService] = None

def get_visualization_service() -> VisualizationService:
    global _visualization_service
    if _visualization_service is None:
        _visualization_service = VisualizationService()
    return _visualization_service
