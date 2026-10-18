#!/bin/zsh
# The job worker with schedules on. launchd runs this (deploy/com.audiobrain.worker.plist); it can also be run by hand.
set -e
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
cd "${0:A:h}/../server"
exec uv run --frozen python manage.py run_jobs
