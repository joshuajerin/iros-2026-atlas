"""Auditable rule-based topic labels for robotics papers."""

from __future__ import annotations

TOPIC_RULES: dict[str, tuple[str, ...]] = {
    "ai_and_foundation_models": ("large language", "llm", "foundation model", "vision-language", "vla", "transformer", "diffusion"),
    "planning_and_decision_making": ("planning", "pomdp", "task and motion", "decision-making", "path finding", "trajectory optimization"),
    "learning_and_reinforcement_learning": ("reinforcement learning", "imitation learning", "policy learning", "deep learning", "neural", "self-supervised"),
    "manipulation_and_grasping": ("manipulation", "grasp", "gripper", "in-hand", "dexterous", "pick and place"),
    "perception_and_vision": ("perception", "vision", "visual", "camera", "depth", "segmentation", "lidar", "point cloud"),
    "navigation_and_mapping": ("navigation", "mapping", "localization", "slam", "exploration", "odometry"),
    "control_and_optimization": ("control", "controller", "model predictive", "mpc", "stability", "optimization", "force control", "calibration", "kinematics"),
    "human_robot_interaction": ("human-robot", "human robot", "shared autonomy", "assistive", "haptic", "collaboration"),
    "medical_and_healthcare_robotics": ("surg", "medical", "clinical", "needle", "laparoscopic", "rehabilitation", "tissue"),
    "aerial_and_field_robotics": ("aerial", "uav", "drone", "agri", "orchard", "underwater", "marine", "space robot"),
    "locomotion_and_legged_robots": ("locomotion", "quadruped", "biped", "gait", "walking", "legged"),
    "soft_and_bioinspired_robotics": ("soft robot", "bio-inspired", "biomimetic", "tendon", "compliant", "pneumatic"),
    "safety_and_reliability": ("safe", "safety", "fault", "risk", "robust", "reliable", "verification"),
}


def classify(*fields: str | None) -> list[str]:
    """Return every matching topic, or ``other_robotics`` as an explicit fallback."""
    haystack = " ".join(value or "" for value in fields).casefold()
    matches = [topic for topic, terms in TOPIC_RULES.items() if any(term in haystack for term in terms)]
    return matches or ["other_robotics"]
