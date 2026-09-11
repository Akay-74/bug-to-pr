"""Metadata schema for a single benchmark candidate."""
from __future__ import annotations

import enum

from pydantic import BaseModel, Field


class ValidationStatus(str, enum.Enum):
    PENDING = "pending"
    VALID = "valid"
    INVALID = "invalid"
    ENVIRONMENT_ERROR = "environment_error"
    TEST_ERROR = "test_error"


class BaselineFailure(BaseModel):
    """A single failing test captured during Stage 0 baseline validation."""

    test_id: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""


class IssueMetadata(BaseModel):
    issue_id: str
    issue_url: str = ""
    repo: str = ""
    base_commit: str = ""
    python_version: str = ""
    # Optional explicit sandbox base image. Defaults to python:<version>-slim.
    # Benchmarks that build native extensions (Cython/C) need a C toolchain,
    # which -slim lacks and the sandbox can't install at validation time (it
    # drops ALL capabilities, so apt-get won't run). For those, point this at
    # a pre-baked image built by backend/docker/build_sandbox_images.sh
    # (e.g. "bug2pr-sandbox:py39-build") rather than a full python:<version>
    # image -- same toolchain, much smaller, built once and reused.
    docker_image: str = ""
    install_command: str = ""
    regression_test_command: str = ""
    # The exact failing-test identifiers (pytest node ids, or the
    # framework's own test label) that regression_test_command targets.
    # Sourced from the issue's real FAIL_TO_PASS data -- never guessed.
    regression_test_ids: list[str] = Field(default_factory=list)
    relevant_test_command: str = ""
    full_test_command: str = ""
    protected_test_paths: list[str] = Field(default_factory=list)
    baseline_failures: list[BaselineFailure] = Field(default_factory=list)
    validation_status: ValidationStatus = ValidationStatus.PENDING

    model_config = {"use_enum_values": False}
