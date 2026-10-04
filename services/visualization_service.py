"""
Visualization Service for InSync.
Universal STEM visualizer combining Plotly.js declarative scientific graphs
with high-fidelity physical motion simulations.
"""

import json
import math
import os
import re
from typing import Dict, Any, Optional

from openai import AsyncOpenAI

from config import get_settings
from utils.logger import get_logger

logger = get_logger(__name__)

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
                    "description": "Use 'plotly' for ML (Logistic Regression, K-Means, Loss curves), Math (Functions, Derivatives), and Kinematics graphs. Use 'physical_simulation' for vehicle braking/skid or projectile trajectories."
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
                        "sim_type": {"type": "string", "enum": ["momentum_stopping", "projectile_motion"]},
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
   - Vehicle momentum & braking (sim_type: "momentum_stopping")
   - Projectile launch trajectory (sim_type: "projectile_motion")
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
    xs = [round(i * 0.1, 2) for i in range(0, 71)]
    sigmoids = [round(1.0 / (1.0 + math.exp(-(w * x + b))), 3) for x in xs]

    # Calculate boundary cutoff x* where sigma(x) = threshold
    # threshold = 1 / (1 + e^{-(wx + b)}) => wx + b = ln(tau / (1 - tau))
    tau = max(0.01, min(threshold, 0.99))
    x_cutoff = round((math.log(tau / (1.0 - tau)) - b) / w, 2)

    data = [
        {
            "x": [0.8, 1.4, 2.0, 2.5],
            "y": [0, 0, 0, 0],
            "mode": "markers",
            "name": "Class 0 (Negative)",
            "marker": {"color": "#ef4444", "size": 10, "symbol": "circle"}
        },
        {
            "x": [3.6, 4.2, 5.0, 6.2],
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
        "xaxis": {"title": "Input Feature Value (x)", "gridcolor": "rgba(255,255,255,0.08)", "zerolinecolor": "rgba(255,255,255,0.2)"},
        "yaxis": {"title": "Predicted Probability P(y=1)", "range": [-0.15, 1.15], "gridcolor": "rgba(255,255,255,0.08)", "zerolinecolor": "rgba(255,255,255,0.2)"},
        "shapes": [
            # Horizontal threshold line
            {"type": "line", "x0": 0, "x1": 7, "y0": threshold, "y1": threshold, "line": {"dash": "dash", "color": "#f59e0b", "width": 2}},
            # Vertical decision cutoff
            {"type": "line", "x0": x_cutoff, "x1": x_cutoff, "y0": 0, "y1": 1, "line": {"dash": "dot", "color": "#38bdf8", "width": 2}}
        ],
        "annotations": [
            {"x": 6.2, "y": threshold + 0.07, "text": f"Threshold τ = {threshold}", "showarrow": False, "font": {"color": "#f59e0b", "size": 11}},
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
            {"param": "threshold", "label": "Decision Threshold (τ)", "min": 0.1, "max": 0.9, "step": 0.05, "default": threshold, "unit": "", "target_type": "threshold_y"},
            {"param": "w", "label": "Weight / Slope (w)", "min": 0.5, "max": 4.0, "step": 0.1, "default": w, "unit": "", "target_type": "scale_y"}
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


def create_kmeans_clustering_spec(k: int = 3) -> Dict[str, Any]:
    """Generates a Plotly interactive specification for K-Means Clustering."""
    data = [
        {"x": [1.2, 1.8, 1.5, 2.2, 1.9], "y": [1.5, 2.2, 1.8, 2.5, 2.0], "mode": "markers", "name": "Cluster A", "marker": {"color": "#3b82f6", "size": 10}},
        {"x": [5.1, 5.8, 5.5, 6.2, 5.9], "y": [5.2, 6.1, 5.8, 6.5, 6.0], "mode": "markers", "name": "Cluster B", "marker": {"color": "#10b981", "size": 10}},
        {"x": [7.8, 8.4, 8.1, 8.9, 8.5], "y": [1.8, 2.4, 2.0, 2.6, 2.2], "mode": "markers", "name": "Cluster C", "marker": {"color": "#f59e0b", "size": 10}},
        {"x": [1.7, 5.7, 8.3], "y": [2.0, 5.9, 2.2], "mode": "markers", "name": "Centroids (μ)", "marker": {"color": "#ec4899", "size": 16, "symbol": "star"}}
    ]

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


def create_fallback_simulation(question_text: str = "") -> Dict[str, Any]:
    """Generates an accurate domain-specific visualization when offline or on API timeout."""
    q_lower = question_text.lower()

    # 1. Machine Learning: Gradient Descent & Cost Minimization (Takes priority over generic 'logistic')
    if any(k in q_lower for k in ["gradient descent", "cost function", "loss function", "minimize the cost", "learning rate"]):
        return create_gradient_descent_spec()

    # 2. Machine Learning: Logistic Regression
    if any(k in q_lower for k in ["logistic", "sigmoid", "classification threshold", "probability threshold"]):
        return create_logistic_regression_spec(w=1.5, b=-4.5, threshold=0.5)

    # 3. Machine Learning: Clustering / K-Means / KNN
    if any(k in q_lower for k in ["cluster", "k-means", "kmeans", "centroid", "knn"]):
        return create_kmeans_clustering_spec(k=3)

    # 3. Physics: Truck / Momentum / Inertia
    if any(k in q_lower for k in ["truck", "momentum", "stopping", "inertia", "brake", "braking", "effort to stop"]):
        physics = compute_momentum_physics(loaded_mass=9000.0, unloaded_mass=3000.0, speed_kmh=60.0, braking_force=8000.0)
        return {
            "engine": "physical_simulation",
            "archetype": "momentum_stopping",
            "title": "Loaded vs. Unloaded Truck: Momentum & Stopping Effort",
            "simulation_spec": {
                "sim_type": "momentum_stopping",
                "parameters": {"loaded_mass": 9000.0, "unloaded_mass": 3000.0, "speed_kmh": 60.0, "braking_force": 8000.0}
            },
            "metrics": physics["metrics"],
            "interactive_controls": [
                {"param": "loaded_mass", "label": "Loaded Truck Mass", "min": 4000, "max": 15000, "step": 500, "default": 9000, "unit": " kg"},
                {"param": "speed_kmh", "label": "Initial Speed", "min": 30, "max": 100, "step": 5, "default": 60, "unit": " km/h"},
                {"param": "braking_force", "label": "Braking Friction Force", "min": 4000, "max": 14000, "step": 500, "default": 8000, "unit": " N"}
            ],
            "explanation": "Momentum is given by p = mass × velocity. At the same speed, a loaded truck has substantially more mass and therefore greater momentum, requiring more braking work and skid distance to stop."
        }

    # 4. Calculus / Maxima / Minima / Optimization / Profit Maximization / Derivatives
    if any(k in q_lower for k in ["maxima", "minima", "maximum", "minimum", "profit maximization", "derivative", "critical point", "concavity", "f'(", "f''("]):
        is_max = not ("minimum" in q_lower or "minima" in q_lower)
        return create_calculus_extrema_spec(is_max=is_max)

    # 5. Kinematics (Distance vs Displacement, Average Speed)
    if any(k in q_lower for k in ["displacement", "scalar", "vector"]):
        return create_kinematics_concept_spec("displacement")
    if any(k in q_lower for k in ["speed", "velocity", "rate of motion", "average speed"]):
        return create_kinematics_concept_spec("speed")

    # 6. Universal Generic STEM Model
    return create_generic_stem_spec(question_text)


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
        r"\b(logistic regression|sigmoid|decision boundary|k-means|clustering|centroid)\b",
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
        self.model = getattr(settings, "LLM_MODEL", "gpt-4o-mini") or "gpt-4o-mini"

    async def plan_simulation(self, question_text: str, context: Optional[str] = None) -> Dict[str, Any]:
        """Plans and generates a generic structured visualization using tool calling."""
        if not self.api_key:
            return create_fallback_simulation(question_text)

        user_content = f"The student is exploring this assignment question:\n\"\"\"{question_text}\"\"\""
        if context:
            user_content += f"\nAdditional Context: {context}"

        q_l = question_text.lower()
        if any(k in q_l for k in ["gradient descent", "cost function", "loss function", "minimize the cost", "learning rate"]):
            user_content += "\nCRITICAL FOCUS: The question is about Gradient Descent / Cost Minimization. Do NOT plot a sigmoid classification curve. Plot the convex Cost Function bowl J(w) vs Weight w, with an interactive slider (param: 'step', target_type: 'gradient_step') stepping downhill to the global minimum."

        user_content += "\nGenerate an appropriate, interactive visualization model tailored to this exact question."

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
                    temperature=0.2,
                )

                choice = response.choices[0]
                if choice.message.tool_calls:
                    call = choice.message.tool_calls[0]
                    arguments = json.loads(call.function.arguments)
                    # Validate engine key
                    if "engine" in arguments and (arguments.get("plotly_spec") or arguments.get("simulation_spec")):
                        if arguments.get("engine") == "plotly" and "plotly_spec" in arguments:
                            spec = arguments["plotly_spec"]
                            layout = spec.setdefault("layout", {})
                            layout.setdefault("paper_bgcolor", "#020617")
                            layout.setdefault("plot_bgcolor", "#0f172a")
                            font = layout.setdefault("font", {})
                            font.setdefault("color", "#94a3b8")
                            font.setdefault("family", "Inter, sans-serif")
                            margin = layout.setdefault("margin", {})
                            margin.setdefault("l", 45)
                            margin.setdefault("r", 25)
                            margin.setdefault("t", 35)
                            margin.setdefault("b", 35)

                            # Enforce spline smoothing on continuous line curves to avoid sharp jagged polygons
                            for trace in spec.get("data", []):
                                if trace.get("mode") and "lines" in trace.get("mode"):
                                    line = trace.setdefault("line", {})
                                    if "shape" not in line and line.get("dash") != "dash" and line.get("dash") != "dot":
                                        line["shape"] = "spline"
                                        line["smoothing"] = 1.3

                            # Infer target_type for interactive controls if omitted
                            for ctrl in arguments.get("interactive_controls", []):
                                if "target_type" not in ctrl:
                                    p = ctrl.get("param", "").lower()
                                    if any(k in p for k in ["step", "iteration", "gradient"]):
                                        ctrl["target_type"] = "gradient_step"
                                    elif any(k in p for k in ["threshold", "tau"]):
                                        ctrl["target_type"] = "threshold_y"
                                    elif any(k in p for k in ["probe", "tangent", "x_probe"]):
                                        ctrl["target_type"] = "tangent_probe"
                                    elif any(k in p for k in ["slope", "speed", "velocity", "rate", "amplitude", "weight", "w", "curvature", "concavity"]):
                                        ctrl["target_type"] = "scale_y"
                                    elif any(k in p for k in ["spread", "variance", "k"]):
                                        ctrl["target_type"] = "cluster_spread"
                                    elif any(k in p for k in ["detour", "winding"]):
                                        ctrl["target_type"] = "detour"
                        return arguments

                return create_fallback_simulation(question_text)

        except Exception as e:
            logger.exception("Error in plan_simulation: %s", e)
            return create_fallback_simulation(question_text)


_visualization_service: Optional[VisualizationService] = None

def get_visualization_service() -> VisualizationService:
    global _visualization_service
    if _visualization_service is None:
        _visualization_service = VisualizationService()
    return _visualization_service
