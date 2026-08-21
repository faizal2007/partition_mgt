# AGENTS.md

## Project overview
- This repository is a small Python project centered on a single disk/partition utility script: [disk_tool.py](disk_tool.py).
- There is no larger framework or test suite yet, so keep changes focused and minimal.
- Prefer standard-library Python 3 patterns unless the project explicitly adds a dependency.

## Working conventions
- Run commands from the repository root.
- Use the local virtual environment when needed: `source venv/bin/activate`.
- Treat [venv/](venv/) as generated environment state; do not edit it directly.
- Keep the script self-contained and readable. Avoid unnecessary abstractions or large refactors.

## Safety expectations
- This project interacts with disk and partition state, so assume commands may be destructive or system-sensitive.
- Before making changes that affect partitions, devices, or file systems, validate the target and prefer explicit confirmation or dry-run behavior.
- Make user-facing output clear about what action is being performed and why.

## Change guidance
- Read the existing implementation before modifying behavior.
- Preserve the current CLI flow unless a requirement explicitly calls for a new interface.
- If adding new functionality, keep it small, documented, and easy to follow from the top of the file.

## Verification
- Use the smallest relevant validation step for the change.
- For Python-only edits, a common check is: `python -m py_compile disk_tool.py`.
- If no automated tests exist, prefer targeted syntax and behavior checks over broad project-wide validation.
