#!/usr/bin/env bash
# Runs linting scripts and type checking
# black - opinionated code formatter
# ruff - lints, finds mistakes, and sorts import statements
# mypy - checks type annotations

set -e

files=(
  "synapse_delete_room_rest_api"
  "tests"
)

# Print out the commands being run
set -x

black "${files[@]}"
ruff --fix "${files[@]}"
mypy "${files[@]}"
