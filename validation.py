"""
Step-level validation functions for each agent's output.

Raises ValidationError with a clear message if an output does not meet
the required structure. Call these immediately after each agent returns
to catch problems before they propagate downstream.
"""


class ValidationError(Exception):
    """Raised when an agent output fails structural validation."""


# ---------------------------------------------------------------------------
# validateDiscovery
# ---------------------------------------------------------------------------

def validate_discovery(result) -> None:
    """
    Validate an EvaluationResult from BountyEvaluator.

    Required fields:
      - should_attempt: bool
      - success_probability: float in [0.0, 1.0]
      - estimated_hours: float >= 0
      - expected_value: float (any sign — constraint enforcement is in evaluator.py)
      - reason: non-empty string
    """
    if not isinstance(result.should_attempt, bool):
        raise ValidationError(
            f"discovery.should_attempt must be bool, got {type(result.should_attempt).__name__}"
        )
    if not isinstance(result.success_probability, (int, float)):
        raise ValidationError(
            f"discovery.success_probability must be a number, got {type(result.success_probability).__name__}"
        )
    if not (0.0 <= result.success_probability <= 1.0):
        raise ValidationError(
            f"discovery.success_probability out of range [0, 1]: {result.success_probability}"
        )
    if not isinstance(result.estimated_hours, (int, float)):
        raise ValidationError(
            f"discovery.estimated_hours must be a number, got {type(result.estimated_hours).__name__}"
        )
    if result.estimated_hours < 0:
        raise ValidationError(
            f"discovery.estimated_hours must be >= 0, got {result.estimated_hours}"
        )
    if not isinstance(result.expected_value, (int, float)):
        raise ValidationError(
            f"discovery.expected_value must be a number, got {type(result.expected_value).__name__}"
        )
    if not result.reason or not str(result.reason).strip():
        raise ValidationError("discovery.reason must be a non-empty string")


# ---------------------------------------------------------------------------
# validatePlan
# ---------------------------------------------------------------------------

def validate_plan(plan) -> None:
    """
    Validate an IssuePlan from IssuePlanner.

    Required:
      - steps: non-empty list of PlanStep objects
      - each step must have a non-empty description
    """
    if not plan.steps:
        raise ValidationError("plan.steps must be a non-empty list")
    if not isinstance(plan.steps, list):
        raise ValidationError(
            f"plan.steps must be a list, got {type(plan.steps).__name__}"
        )
    for i, step in enumerate(plan.steps):
        desc = getattr(step, "description", None)
        if not desc or not str(desc).strip():
            raise ValidationError(f"plan.steps[{i}].description is empty or missing")
    if not isinstance(plan.all_files, list):
        raise ValidationError(
            f"plan.all_files must be a list, got {type(plan.all_files).__name__}"
        )


# ---------------------------------------------------------------------------
# validateBuild
# ---------------------------------------------------------------------------

def validate_build(result) -> None:
    """
    Validate an ImplementationResult from SeniorEngineer.

    Required:
      - succeeded: True
      - updated_files: non-empty list
      - each file must have file_path (str) and content (str)
    """
    if not result.succeeded:
        raise ValidationError("build.succeeded is False — implementation did not complete")
    if not result.updated_files:
        raise ValidationError("build.updated_files must be a non-empty list")
    if not isinstance(result.updated_files, list):
        raise ValidationError(
            f"build.updated_files must be a list, got {type(result.updated_files).__name__}"
        )
    for i, f in enumerate(result.updated_files):
        path = getattr(f, "file_path", None)
        if not path or not str(path).strip():
            raise ValidationError(f"build.updated_files[{i}].file_path is empty or missing")
        content = getattr(f, "content", None)
        if content is None:
            raise ValidationError(f"build.updated_files[{i}].content is None")
        if not isinstance(content, str):
            raise ValidationError(
                f"build.updated_files[{i}].content must be str, got {type(content).__name__}"
            )


# ---------------------------------------------------------------------------
# validateReview
# ---------------------------------------------------------------------------

def validate_review(review) -> None:
    """
    Validate a ReviewResult from PRReviewer.

    Required:
      - approve: bool
      - issues: list of strings
      - fix_priority: one of "low", "medium", "high"
    """
    if not isinstance(review.approve, bool):
        raise ValidationError(
            f"review.approve must be bool, got {type(review.approve).__name__}"
        )
    if not isinstance(review.issues, list):
        raise ValidationError(
            f"review.issues must be a list, got {type(review.issues).__name__}"
        )
    for i, issue in enumerate(review.issues):
        if not isinstance(issue, str):
            raise ValidationError(f"review.issues[{i}] must be str, got {type(issue).__name__}")
    valid_priorities = ("low", "medium", "high")
    if review.fix_priority not in valid_priorities:
        raise ValidationError(
            f"review.fix_priority must be one of {valid_priorities}, got {review.fix_priority!r}"
        )
