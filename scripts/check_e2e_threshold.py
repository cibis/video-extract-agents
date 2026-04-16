"""
Check whether the e2e test failure rate is within the configured threshold.

Reads ci-logs/pytest-results.xml (JUnit XML produced by pytest --junit-xml).
Exits 0 if the failure percentage is within E2E_FAILURE_THRESHOLD_PCT (default 25).
Exits 1 if the threshold is exceeded or no tests ran.

Set E2E_FAILURE_THRESHOLD_PCT=0 to require all tests to pass.
"""
import os
import sys
import xml.etree.ElementTree as ET

RESULTS_FILE = "ci-logs/pytest-results.xml"


def main() -> None:
    threshold = float(os.environ.get("E2E_FAILURE_THRESHOLD_PCT", "25"))

    try:
        tree = ET.parse(RESULTS_FILE)
    except FileNotFoundError:
        print(f"ERROR: {RESULTS_FILE} not found — pytest may have crashed before collecting tests")
        sys.exit(1)

    root = tree.getroot()
    suite = root if root.tag == "testsuite" else root.find("testsuite")

    total   = int(suite.attrib.get("tests",    0))
    failed  = int(suite.attrib.get("failures", 0)) + int(suite.attrib.get("errors", 0))
    skipped = int(suite.attrib.get("skipped",  0))
    ran     = total - skipped

    if ran == 0:
        print("ERROR: no tests ran (all skipped or none collected)")
        sys.exit(1)

    pct = failed / ran * 100
    print(f"Results : {total} collected, {skipped} skipped, {ran} ran, {failed} failed ({pct:.1f}%)")
    print(f"Threshold: {threshold}%")

    if threshold == 0 and failed > 0:
        print(f"FAIL: {failed} failure(s) — threshold is 0 (all tests must pass)")
        sys.exit(1)
    elif threshold > 0 and pct > threshold:
        print(f"FAIL: {pct:.1f}% failures exceed {threshold}% threshold")
        sys.exit(1)
    else:
        print("PASS: failure rate is within the acceptable threshold")


if __name__ == "__main__":
    main()
