#!/usr/bin/env bash
# Run quality and audit tests after code changes.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="${ROOT_DIR}/backend"
VENV_PY="${BACKEND_DIR}/.venv/bin/python"
VENV_TEST="${BACKEND_DIR}/.venv-test/bin/pytest"

if [[ -x "${BACKEND_DIR}/.venv-test/bin/pytest" ]]; then
  PYTEST="${VENV_TEST}"
elif [[ -x "${BACKEND_DIR}/.venv/bin/pytest" ]]; then
  PYTEST="${BACKEND_DIR}/.venv/bin/pytest"
else
  PYTEST="pytest"
fi

cd "${BACKEND_DIR}"
export PYTHONPATH="${BACKEND_DIR}${PYTHONPATH:+:${PYTHONPATH}}"

echo "Running mandatory command audit tests..."
${PYTEST} -q -m audit

echo "Running prompt quality tests..."
${PYTEST} -q \
  tests/test_prompt_intent_matrix.py \
  tests/test_prompt_task_board_outcomes.py \
  tests/test_prompt_orchestration_outcomes.py \
  tests/test_prompt_path_extraction.py \
  tests/test_command_audit.py \
  tests/test_command_audit_mandatory.py \
  tests/test_read_intent.py \
  tests/test_named_folder_intent.py \
  tests/test_shell_commands.py \
  tests/test_tools.py

echo "All quality checks passed."
