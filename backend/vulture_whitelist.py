# Vulture whitelist — parameters required by callback/protocol signatures
# that vulture incorrectly reports as unused.
#
# Run vulture with: poetry run vulture app/ vulture_whitelist.py --min-confidence 80

# Signal handler signature (signum, frame)
frame  # unused variable

# Context manager __exit__(exc_type, exc_val, exc_tb)
exc_type  # unused variable
exc_val  # unused variable
exc_tb  # unused variable
