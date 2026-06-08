"""Module-by-module validation tests for the cribl_audit package."""

import csv
import io
import json
import os
import sys
import tempfile

passed = 0
failed = 0


def ok(label: str) -> None:
    global passed
    passed += 1
    print(f"    PASS  {label}")


def fail(label: str, detail: str = "") -> None:
    global failed
    failed += 1
    print(f"    FAIL  {label}  {detail}")


# ====================================================================
# 1. constants
# ====================================================================
print("\n=== Module: constants ===")
from cribl_audit.constants import (
    CONNECT_TIMEOUT, READ_TIMEOUT, CAPTURE_READ_TIMEOUT_PAD,
    EXIT_OK, EXIT_ERROR, EXIT_PARTIAL, EXIT_INTERRUPTED,
    CRIBL_CLOUD_LOGIN_URL, CRIBL_CLOUD_AUDIENCE,
)
assert CONNECT_TIMEOUT == 10; ok("CONNECT_TIMEOUT")
assert READ_TIMEOUT == 30; ok("READ_TIMEOUT")
assert EXIT_OK == 0; ok("EXIT_OK")
assert EXIT_ERROR == 1; ok("EXIT_ERROR")
assert EXIT_PARTIAL == 2; ok("EXIT_PARTIAL")
assert EXIT_INTERRUPTED == 130; ok("EXIT_INTERRUPTED")
assert "login.cribl.cloud" in CRIBL_CLOUD_LOGIN_URL; ok("CRIBL_CLOUD_LOGIN_URL")
assert "api.cribl.cloud" in CRIBL_CLOUD_AUDIENCE; ok("CRIBL_CLOUD_AUDIENCE")


# ====================================================================
# 2. exceptions
# ====================================================================
print("\n=== Module: exceptions ===")
from cribl_audit.exceptions import CriblAPIError, AuthenticationError

try:
    raise AuthenticationError("test")
except AuthenticationError as e:
    assert str(e) == "test"; ok("AuthenticationError")

# CriblAPIError needs a Response object — test structure
assert issubclass(CriblAPIError, Exception); ok("CriblAPIError is Exception")
assert issubclass(AuthenticationError, Exception); ok("AuthenticationError is Exception")


# ====================================================================
# 3. http
# ====================================================================
print("\n=== Module: http ===")
from cribl_audit.http import build_session, raise_for_status

session = build_session(verify_ssl=True)
assert session.verify is True; ok("build_session verify=True")

session2 = build_session(verify_ssl=False)
assert session2.verify is False; ok("build_session verify=False")

# Check retry adapter is mounted
assert "https://" in session.adapters; ok("HTTPS adapter mounted")
assert "http://" in session.adapters; ok("HTTP adapter mounted")


# ====================================================================
# 4. config
# ====================================================================
print("\n=== Module: config ===")
from cribl_audit.config import load_config, load_env_file

# Test load_config with a temp file
tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
json.dump({
    "auth": {"cribl_url": "https://test:9000", "username": "admin"},
    "capture": {"groups": ["grp1"], "seconds": 60, "rounds": 5},
    "matching": {"mode": "contains"},
    "output": {"format": "both", "lookup": "APP_test.json"},
    "logging": {"verbose": True},
}, tmp)
tmp.close()

# Clear env vars that config would set
for k in ["CRIBL_URL", "CRIBL_USERNAME"]:
    os.environ.pop(k, None)

defaults = load_config(tmp.name)
os.unlink(tmp.name)

assert defaults.get("group") == ["grp1"]; ok("config: groups -> group mapping")
assert defaults.get("seconds") == 60; ok("config: seconds")
assert defaults.get("rounds") == 5; ok("config: rounds")
assert defaults.get("match_mode") == "contains"; ok("config: match_mode")
assert defaults.get("format") == "both"; ok("config: format")
assert defaults.get("lookup") == "APP_test.json"; ok("config: lookup")
assert defaults.get("verbose") is True; ok("config: verbose")
assert os.environ.get("CRIBL_URL") == "https://test:9000"; ok("config: auth -> env CRIBL_URL")
assert os.environ.get("CRIBL_USERNAME") == "admin"; ok("config: auth -> env CRIBL_USERNAME")

# Test load_env_file
env_tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False)
env_tmp.write('TEST_CRIBL_VAR="hello_world"\n')
env_tmp.write("export TEST_CRIBL_VAR2=value2\n")
env_tmp.write("# comment line\n")
env_tmp.close()
os.environ.pop("TEST_CRIBL_VAR", None)
os.environ.pop("TEST_CRIBL_VAR2", None)
load_env_file(env_tmp.name)
os.unlink(env_tmp.name)
assert os.environ.get("TEST_CRIBL_VAR") == "hello_world"; ok("env_file: quoted value")
assert os.environ.get("TEST_CRIBL_VAR2") == "value2"; ok("env_file: export prefix")
os.environ.pop("TEST_CRIBL_VAR", None)
os.environ.pop("TEST_CRIBL_VAR2", None)


# ====================================================================
# 5. matching
# ====================================================================
print("\n=== Module: matching ===")
from cribl_audit.matching import get_nested, extract_appid, match_appid_to_dest, check_lookup_route_dest_status

# get_nested
assert get_nested({"a": {"b": "c"}}, "a.b") == "c"; ok("get_nested: deep path")
assert get_nested({"a": 1}, "a") == 1; ok("get_nested: top-level")
assert get_nested({"a": 1}, "b") is None; ok("get_nested: missing key")
assert get_nested({"a": 1}, "a.b") is None; ok("get_nested: non-dict child")

# extract_appid
ev = {"apmId": "my-app", "appName": "MyApp"}
assert extract_appid(ev, "apmId") == "my-app"; ok("extract_appid: top-level")
ev2 = {"_raw": '{"apmId": "from-raw"}'}
assert extract_appid(ev2, "apmId") == "from-raw"; ok("extract_appid: from _raw")
assert extract_appid({}, "apmId") is None; ok("extract_appid: missing")

# match_appid_to_dest
dests = [
    {"id": "azure_blob:prod-app-one", "type": "azure_blob", "containerName": "app-one"},
    {"id": "azure_blob:prod-app-two", "type": "azure_blob", "containerName": "app-two"},
]
assert match_appid_to_dest("app-one", dests, "exact") == "azure_blob:prod-app-one"; ok("match: exact hit")
assert match_appid_to_dest("APP-ONE", dests, "exact") == "azure_blob:prod-app-one"; ok("match: exact case-insensitive")
assert match_appid_to_dest("unknown", dests, "exact") is None; ok("match: exact miss")
assert match_appid_to_dest("one", dests, "contains") == "azure_blob:prod-app-one"; ok("match: contains")
assert match_appid_to_dest("xyz", dests, "contains") is None; ok("match: contains miss")

# check_lookup_route_dest_status
routes = [
    {"id": "route-app-one-blob", "name": "route-app-one-blob", "filter": "true", "output": "azure_blob:prod-app-one"},
    {"id": "route-misc", "name": "general-route", "filter": "apmId=='app-three'", "output": "azure_blob:other"},
]

# CONFIGURED
results = check_lookup_route_dest_status({"app-one"}, dests, routes, "exact")
assert results[0]["has_destination"] is True; ok("audit: CONFIGURED dest")
assert results[0]["has_route"] is True; ok("audit: CONFIGURED route")
assert "CONFIGURED" in results[0]["status"]; ok("audit: CONFIGURED status")

# MISSING ROUTE
results = check_lookup_route_dest_status({"app-two"}, dests, routes, "exact")
assert results[0]["has_destination"] is True; ok("audit: MISSING ROUTE dest")
assert results[0]["has_route"] is False; ok("audit: MISSING ROUTE route")

# MISSING BOTH
results = check_lookup_route_dest_status({"unknown"}, dests, routes, "exact")
assert results[0]["has_destination"] is False and results[0]["has_route"] is False; ok("audit: MISSING BOTH")

# MISSING DESTINATION (route filter match)
results = check_lookup_route_dest_status({"app-three"}, dests, routes, "exact")
assert results[0]["has_destination"] is False and results[0]["has_route"] is True; ok("audit: MISSING DEST")

# Dest ID fallback
results = check_lookup_route_dest_status({"prod-app-one"}, dests, [], "exact")
assert results[0]["has_destination"] is True; ok("audit: dest ID substring fallback")

# Route name fallback
r2 = [{"id": "r1", "name": "prod-my-special-app-route", "filter": "true", "output": "x"}]
results = check_lookup_route_dest_status({"my-special-app"}, [], r2, "exact")
assert results[0]["has_route"] is True; ok("audit: route name substring")

# Multiple appIds sorted
results = check_lookup_route_dest_status({"z-app", "a-app"}, [], [], "exact")
assert [r["apmId"] for r in results] == ["a-app", "z-app"]; ok("audit: sorted output")


# ====================================================================
# 6. lookup
# ====================================================================
print("\n=== Module: lookup ===")
from cribl_audit.lookup import load_lookup_appids, load_previous_unmatched, load_all_known_appids, find_latest_csv

# load_lookup_appids
tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
json.dump({"azure_storage_account_containers": ["App-One", "app-two", "APP-THREE"]}, tmp)
tmp.close()
ids = load_lookup_appids(tmp.name)
os.unlink(tmp.name)
assert ids == {"app-one", "app-two", "app-three"}; ok("lookup: loads + lowercases")

# load_lookup_appids with missing file
ids = load_lookup_appids("/nonexistent/path.json")
assert ids == set(); ok("lookup: missing file returns empty")

# load_previous_unmatched
tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="")
writer = csv.DictWriter(tmp, fieldnames=["apmId", "appName", "outputId", "matched_destination", "event_count"])
writer.writeheader()
writer.writerow({"apmId": "x", "appName": "", "outputId": "", "matched_destination": "DEFAULT", "event_count": "1"})
writer.writerow({"apmId": "y", "appName": "", "outputId": "", "matched_destination": "azure_blob:dest", "event_count": "2"})
tmp.close()
prev = load_previous_unmatched(tmp.name)
assert prev == {"x"}; ok("lookup: load_previous_unmatched")

known = load_all_known_appids(tmp.name)
assert known == {"x", "y"}; ok("lookup: load_all_known_appids")
os.unlink(tmp.name)


# ====================================================================
# 7. output
# ====================================================================
print("\n=== Module: output ===")
from cribl_audit.output import write_csv, write_json, write_lookup_status_csv, print_results_table, print_lookup_status_table

# write_csv
tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False)
tmp.close()
old_stdout = sys.stdout
sys.stdout = io.StringIO()
write_csv([("a", "A", "out1", "DEFAULT", 10)], tmp.name, False)
sys.stdout = old_stdout
with open(tmp.name, newline="", encoding="utf-8") as f:
    rows = list(csv.DictReader(f))
assert len(rows) == 1 and rows[0]["apmId"] == "a"; ok("output: write_csv")
os.unlink(tmp.name)

# write_json
tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
tmp.close()
sys.stdout = io.StringIO()
write_json([("b", "B", "out2", "dest1", 5)], tmp.name, "grp", 100)
sys.stdout = old_stdout
with open(tmp.name, encoding="utf-8") as f:
    data = json.load(f)
assert data["total_events"] == 100; ok("output: write_json metadata")
assert data["results"][0]["apmId"] == "b"; ok("output: write_json data")
os.unlink(tmp.name)

# write_lookup_status_csv
tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False)
tmp.close()
test_results = [
    {"apmId": "x", "has_destination": True, "destination_id": "d1",
     "has_route": False, "route_id": "NONE", "route_output": "NONE", "status": "MISSING ROUTE"},
]
sys.stdout = io.StringIO()
write_lookup_status_csv(test_results, tmp.name)
sys.stdout = old_stdout
with open(tmp.name, newline="", encoding="utf-8") as f:
    rows = list(csv.DictReader(f))
assert rows[0]["apmId"] == "x" and rows[0]["status"] == "MISSING ROUTE"; ok("output: write_lookup_status_csv")
os.unlink(tmp.name)

# print_results_table (no crash)
sys.stdout = io.StringIO()
print_results_table([("a", "A", "out", "DEFAULT", 1)], {"a"}, {"a"}, 10)
out = sys.stdout.getvalue()
sys.stdout = old_stdout
assert "a" in out and "<<<" in out; ok("output: print_results_table")

# print_lookup_status_table (no crash)
sys.stdout = io.StringIO()
print_lookup_status_table(test_results)
out = sys.stdout.getvalue()
sys.stdout = old_stdout
assert "MISSING ROUTE" in out and "<<<" in out; ok("output: print_lookup_status_table")

# Empty inputs
sys.stdout = io.StringIO()
print_lookup_status_table([])
sys.stdout = old_stdout
ok("output: empty lookup table no crash")


# ====================================================================
# 8. elasticsearch
# ====================================================================
print("\n=== Module: elasticsearch ===")
from cribl_audit.elasticsearch import ElasticsearchClient, build_es_client

# Instantiation
es = ElasticsearchClient("https://elk:9200", "test-index", api_key="key123")
assert es._url == "https://elk:9200"; ok("elasticsearch: url strip")
assert es._index == "test-index"; ok("elasticsearch: index")
assert "ApiKey key123" in es._session.headers.get("Authorization", ""); ok("elasticsearch: api_key header")

# build_es_client returns None when not configured
class FakeArgs:
    es_url = None
    es_index = None
    no_verify_ssl = False
assert build_es_client(FakeArgs()) is None; ok("elasticsearch: build returns None")

# build_es_client returns client when configured
os.environ["ES_URL"] = "https://test:9200"
os.environ["ES_INDEX"] = "idx"
os.environ.pop("ES_API_KEY", None)
result = build_es_client(FakeArgs())
assert result is not None; ok("elasticsearch: build from env")
os.environ.pop("ES_URL", None)
os.environ.pop("ES_INDEX", None)


# ====================================================================
# 9. auth
# ====================================================================
print("\n=== Module: auth ===")
from cribl_audit.auth import CriblAuth

# Static token auth
os.environ["CRIBL_TOKEN"] = "test-token-123"
os.environ.pop("CRIBL_CLIENT_ID", None)
os.environ.pop("CRIBL_CLIENT_SECRET", None)
os.environ.pop("CRIBL_USERNAME", None)
os.environ.pop("CRIBL_PASSWORD", None)

auth_obj = CriblAuth("https://test:9000", build_session())
token = auth_obj.token
assert token == "test-token-123"; ok("auth: static token")
os.environ.pop("CRIBL_TOKEN", None)

# No credentials -> AuthenticationError
os.environ.pop("CRIBL_TOKEN", None)
auth_obj2 = CriblAuth("https://test:9000", build_session())
try:
    _ = auth_obj2.token
    fail("auth: should raise AuthenticationError")
except AuthenticationError:
    ok("auth: no creds raises AuthenticationError")


# ====================================================================
# 10. client
# ====================================================================
print("\n=== Module: client ===")
from cribl_audit.client import CriblClient

# Check group property
os.environ["CRIBL_TOKEN"] = "test"
auth_c = CriblAuth("https://test:9000", build_session())
c = CriblClient("https://test:9000", "prod-workers", auth_c, build_session())
assert c.group == "prod-workers"; ok("client: group property")
assert "/api/v1/m/prod-workers" in c._base; ok("client: base URL")
os.environ.pop("CRIBL_TOKEN", None)


# ====================================================================
# 11. analysis (unit-testable parts)
# ====================================================================
print("\n=== Module: analysis ===")
from cribl_audit.analysis import resolve_default_output_id

# resolve_default_output_id from env
os.environ["CRIBL_DEFAULT_OUTPUT_ID"] = "azure_blob:default-test"
os.environ["CRIBL_TOKEN"] = "test"
auth_a = CriblAuth("https://test:9000", build_session())
c_a = CriblClient("https://test:9000", "grp", auth_a, build_session())
result = resolve_default_output_id(c_a)
assert result == "azure_blob:default-test"; ok("analysis: resolve from env")
os.environ.pop("CRIBL_DEFAULT_OUTPUT_ID", None)
os.environ.pop("CRIBL_TOKEN", None)


# ====================================================================
# 12. cli
# ====================================================================
print("\n=== Module: cli ===")
from cribl_audit.cli import _build_parser

parser = _build_parser()
args = parser.parse_args(["--group", "g1", "g2", "--seconds", "60", "--rounds", "3"])
assert args.group == ["g1", "g2"]; ok("cli: --group multi")
assert args.seconds == 60; ok("cli: --seconds")
assert args.rounds == 3; ok("cli: --rounds")

args2 = parser.parse_args(["--group", "g1", "--inspect", "--dry-run", "--append"])
assert args2.inspect is True; ok("cli: --inspect")
assert args2.dry_run is True; ok("cli: --dry-run")
assert args2.append is True; ok("cli: --append")

args3 = parser.parse_args(["--group", "g1", "--match-mode", "partition", "--level", "2"])
assert args3.match_mode == "partition"; ok("cli: --match-mode")
assert args3.level == 2; ok("cli: --level")


# ====================================================================
# 13. Integration: lookup_hitting_default logic
# ====================================================================
print("\n=== Integration: lookup_hitting_default ===")

default_id = "azure_blob:company-default"
all_rows = [
    ("app-one", "App One", "azure_blob:company-default", "azure_blob:prod-app-one", 42),
    ("app-two", "App Two", "azure_blob:company-default", "DEFAULT", 15),
    ("new-app", "New App", "azure_blob:company-default", "DEFAULT", 3),
]
all_app_ids = {r[0] for r in all_rows}
lookup_appids = {"app-one", "app-two"}

lookup_hitting_default = {
    aid for aid in all_app_ids
    if aid.lower() in lookup_appids
    and any(r[0] == aid and default_id in r[2] for r in all_rows)
}
assert lookup_hitting_default == {"app-one", "app-two"}; ok("integration: lookup hitting default")
assert "new-app" not in lookup_hitting_default; ok("integration: non-lookup excluded")

# Old r[3]=='DEFAULT' would miss app-one
old_logic = {
    aid for aid in all_app_ids
    if aid.lower() in lookup_appids
    and any(r[0] == aid and r[3] == "DEFAULT" for r in all_rows)
}
assert "app-one" not in old_logic; ok("integration: old logic misses app-one")
assert "app-one" in lookup_hitting_default; ok("integration: new logic catches app-one")


# ====================================================================
# Summary
# ====================================================================
print()
print("=" * 60)
print(f"  {passed} passed, {failed} failed")
if failed:
    print("  SOME TESTS FAILED")
    sys.exit(1)
else:
    print("  ALL TESTS PASSED")
print("=" * 60)
