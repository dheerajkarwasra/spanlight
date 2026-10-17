# Multi-step developer tasks. CI does not use this file; the commands it runs are in ci.yml.

# The live harness runs in the backend environment with the local Python SDK added.
RUN := uv run --with ../sdks/python python -m

.PHONY: doctor-accuracy

# The Doctor accuracy harness, live (accuracy/doctor/README.md). Needs the API on :8000 and the
# worker running, the price book synced and ANTHROPIC_API_KEY set; it spends real money (the
# first step prints the estimate). Takes 10 to 15 minutes; the trigger steps wait on the worker.
doctor-accuracy:
	@test -n "$$ANTHROPIC_API_KEY" || { echo "ANTHROPIC_API_KEY is not set" >&2; exit 1; }
	$(RUN) accuracy.doctor.runner --plan
	$(RUN) accuracy.setup
	$(RUN) accuracy.doctor.runner --setup
	$(RUN) accuracy.doctor.runner --baseline
	$(RUN) accuracy.doctor.trigger --rollup-only
	$(RUN) accuracy.doctor.runner --run
	$(RUN) accuracy.doctor.trigger
	$(RUN) accuracy.doctor.runner --collect
	$(RUN) accuracy.doctor.score
