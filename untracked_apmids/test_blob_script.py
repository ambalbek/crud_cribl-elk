#!/usr/bin/env python3
"""Deep validation tests for get_apmids_from_blob.py"""

import gzip
import io
import json
import sys
import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

# Add script dir to path
sys.path.insert(0, os.path.dirname(__file__))

# Mock azure.storage.blob before importing the script
mock_blob_module = MagicMock()
sys.modules["azure.storage.blob"] = mock_blob_module
mock_blob_module.BlobServiceClient = MagicMock
mock_blob_module.ContainerClient = MagicMock

import get_apmids_from_blob as blob

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def make_gzipped_json_lines(events: list[dict]) -> bytes:
    """Create gzipped NDJSON bytes from a list of dicts."""
    buf = io.BytesIO()
    with gzip.open(buf, "wt", encoding="utf-8") as gz:
        for event in events:
            gz.write(json.dumps(event) + "\n")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 1. parse_blob_path
# ---------------------------------------------------------------------------

def test_parse_blob_path_valid():
    result = blob.parse_blob_path("2026/06/17/my-app/ftw/prod/CriblOut-0001.json.gz")
    assert result is not None, "Should parse valid path"
    assert result["date"] == "2026/06/17"
    assert result["appName"] == "my-app"
    assert result["region"] == "ftw"
    assert result["env"] == "prod"
    assert result["filename"] == "CriblOut-0001.json.gz"
    print("  PASS: parse_blob_path — valid path")

def test_parse_blob_path_too_short():
    result = blob.parse_blob_path("2026/06/17/my-app")
    assert result is None, "Should reject short path"
    print("  PASS: parse_blob_path — rejects short path")

def test_parse_blob_path_wrong_filename():
    result = blob.parse_blob_path("2026/06/17/my-app/ftw/prod/other-file.log")
    assert result is None, "Should reject non-CriblOut files"
    print("  PASS: parse_blob_path — rejects non-CriblOut filename")

def test_parse_blob_path_not_gzipped():
    result = blob.parse_blob_path("2026/06/17/my-app/ftw/prod/CriblOut-0001.json")
    assert result is None, "Should reject non-.json.gz files"
    print("  PASS: parse_blob_path — rejects non-.json.gz")

def test_parse_blob_path_deeper_nesting():
    # Extra path segments should still work (parts[-1] is filename)
    result = blob.parse_blob_path("2026/06/17/my-app/ftw/prod/CriblOut-abc.json.gz")
    assert result is not None
    assert result["appName"] == "my-app"
    print("  PASS: parse_blob_path — standard depth")


# ---------------------------------------------------------------------------
# 2. generate_date_prefixes
# ---------------------------------------------------------------------------

def test_generate_date_prefixes_today():
    prefixes = blob.generate_date_prefixes(1)
    assert len(prefixes) == 1, f"Expected 1 prefix, got {len(prefixes)}"
    today = datetime.now(timezone.utc).strftime("%Y/%m/%d")
    assert prefixes[0] == today, f"Expected {today}, got {prefixes[0]}"
    print("  PASS: generate_date_prefixes — 1 day = today")

def test_generate_date_prefixes_multi():
    prefixes = blob.generate_date_prefixes(3)
    assert len(prefixes) == 3, f"Expected 3 prefixes, got {len(prefixes)}"
    # First should be today, last should be 2 days ago
    assert prefixes[0] > prefixes[-1], "Prefixes should be newest first"
    print("  PASS: generate_date_prefixes — 3 days")

def test_generate_date_prefixes_format():
    prefixes = blob.generate_date_prefixes(1)
    parts = prefixes[0].split("/")
    assert len(parts) == 3, "Format should be YYYY/MM/DD"
    assert len(parts[0]) == 4, "Year should be 4 digits"
    assert len(parts[1]) == 2, "Month should be 2 digits"
    assert len(parts[2]) == 2, "Day should be 2 digits"
    print("  PASS: generate_date_prefixes — format YYYY/MM/DD")


# ---------------------------------------------------------------------------
# 3. check_route_dest_status (matching logic)
# ---------------------------------------------------------------------------

def test_matching_configured():
    apmids = [{"apmId": "myapp", "appName": "MyApp", "event_count": 10}]
    destinations = [{"id": "azure_blob:myapp-dest", "containerName": "myapp-container", "type": "azure_blob"}]
    routes = [{"id": "route-1", "name": "myapp-route"}]
    results = blob.check_route_dest_status(apmids, destinations, routes)
    assert len(results) == 1
    assert results[0]["status"] == "CONFIGURED"
    assert results[0]["has_destination"] is True
    assert results[0]["has_route"] is True
    assert results[0]["event_count"] == 10
    print("  PASS: matching — CONFIGURED status")

def test_matching_missing_both():
    apmids = [{"apmId": "unknown-app", "appName": "Unknown", "event_count": 5}]
    destinations = [{"id": "azure_blob:other", "containerName": "other-container"}]
    routes = [{"id": "route-1", "name": "other-route"}]
    results = blob.check_route_dest_status(apmids, destinations, routes)
    assert results[0]["status"] == "MISSING_BOTH"
    assert results[0]["destination_id"] == "NONE"
    assert results[0]["route_id"] == "NONE"
    print("  PASS: matching — MISSING_BOTH status")

def test_matching_missing_route():
    apmids = [{"apmId": "myapp", "appName": "MyApp", "event_count": 3}]
    destinations = [{"id": "dest-1", "containerName": "myapp-data"}]
    routes = [{"id": "route-1", "name": "other-route"}]
    results = blob.check_route_dest_status(apmids, destinations, routes)
    assert results[0]["status"] == "MISSING_ROUTE"
    print("  PASS: matching — MISSING_ROUTE status")

def test_matching_missing_destination():
    apmids = [{"apmId": "myapp", "appName": "MyApp", "event_count": 3}]
    destinations = [{"id": "dest-1", "containerName": "other-container"}]
    routes = [{"id": "route-1", "name": "myapp-route"}]
    results = blob.check_route_dest_status(apmids, destinations, routes)
    assert results[0]["status"] == "MISSING_DESTINATION"
    print("  PASS: matching — MISSING_DESTINATION status")

def test_matching_case_insensitive():
    apmids = [{"apmId": "MyApp", "appName": "MyApp", "event_count": 1}]
    destinations = [{"id": "dest-1", "containerName": "MYAPP-container"}]
    routes = [{"id": "route-1", "name": "MYAPP-route"}]
    results = blob.check_route_dest_status(apmids, destinations, routes)
    assert results[0]["status"] == "CONFIGURED"
    print("  PASS: matching — case insensitive")

def test_matching_empty_inputs():
    results = blob.check_route_dest_status([], [], [])
    assert results == []
    print("  PASS: matching — empty inputs")

def test_matching_multiple_apmids():
    apmids = [
        {"apmId": "app-a", "appName": "A", "event_count": 10},
        {"apmId": "app-b", "appName": "B", "event_count": 20},
        {"apmId": "app-c", "appName": "C", "event_count": 30},
    ]
    destinations = [{"id": "dest-a", "containerName": "app-a-container"}]
    routes = [{"id": "route-a", "name": "app-a-route"}, {"id": "route-c", "name": "app-c-route"}]
    results = blob.check_route_dest_status(apmids, destinations, routes)
    statuses = {r["apmId"]: r["status"] for r in results}
    assert statuses["app-a"] == "CONFIGURED"
    assert statuses["app-b"] == "MISSING_BOTH"
    assert statuses["app-c"] == "MISSING_DESTINATION"
    print("  PASS: matching — multiple apmIds with mixed statuses")


# ---------------------------------------------------------------------------
# 4. fetch_apmids_from_blob (mocked blob interaction)
# ---------------------------------------------------------------------------

def test_fetch_apmids_reads_first_2_lines_only():
    """Verify we stop after reading 2 lines with apmId."""
    events = [
        {"apmId": "app1", "appName": "App One", "msg": "line1"},
        {"apmId": "app1", "appName": "App One", "msg": "line2"},
        {"apmId": "app1", "appName": "App One", "msg": "line3 — should NOT be read"},
    ]
    gz_data = make_gzipped_json_lines(events)

    today = datetime.now(timezone.utc).strftime("%Y/%m/%d")
    blob_name = f"{today}/testapp/ftw/prod/CriblOut-0001.json.gz"

    mock_blob = MagicMock()
    mock_blob.name = blob_name

    mock_container = MagicMock()
    mock_app_dir = MagicMock()
    mock_app_dir.name = f"{today}/testapp/"
    mock_container.walk_blobs.return_value = [mock_app_dir]

    # Only return the blob for the ftw region prefix
    def list_blobs_side_effect(name_starts_with=""):
        if "/ftw/" in name_starts_with:
            return [mock_blob]
        return []
    mock_container.list_blobs.side_effect = list_blobs_side_effect

    mock_download = MagicMock()
    mock_download.readall.return_value = gz_data
    mock_container.download_blob.return_value = mock_download

    results = blob.fetch_apmids_from_blob(mock_container, days=1)
    assert len(results) == 1
    assert results[0]["apmId"] == "app1"
    assert results[0]["event_count"] == 2, f"Expected 2 events (first 2 lines), got {results[0]['event_count']}"
    print("  PASS: fetch — reads only first 2 lines per blob")


def test_fetch_apmids_multiple_apps():
    """Multiple blobs from different appName dirs."""
    today = datetime.now(timezone.utc).strftime("%Y/%m/%d")

    events_a = [{"apmId": "apm-a", "appName": "AppA"}]
    events_b = [{"apmId": "apm-b", "appName": "AppB"}]

    blob_a = MagicMock()
    blob_a.name = f"{today}/app-a/ftw/prod/CriblOut-0001.json.gz"
    blob_b = MagicMock()
    blob_b.name = f"{today}/app-b/azn/dev/CriblOut-0001.json.gz"

    mock_container = MagicMock()

    # walk_blobs returns 2 app dirs
    dir_a = MagicMock(); dir_a.name = f"{today}/app-a/"
    dir_b = MagicMock(); dir_b.name = f"{today}/app-b/"
    mock_container.walk_blobs.return_value = [dir_a, dir_b]

    # list_blobs returns different blobs based on prefix
    def list_blobs_side_effect(name_starts_with=""):
        if "app-a/ftw/" in name_starts_with:
            return [blob_a]
        elif "app-b/azn/" in name_starts_with:
            return [blob_b]
        return []
    mock_container.list_blobs.side_effect = list_blobs_side_effect

    def download_side_effect(name):
        mock_dl = MagicMock()
        if "app-a" in name:
            mock_dl.readall.return_value = make_gzipped_json_lines(events_a)
        else:
            mock_dl.readall.return_value = make_gzipped_json_lines(events_b)
        return mock_dl
    mock_container.download_blob.side_effect = download_side_effect

    results = blob.fetch_apmids_from_blob(mock_container, days=1)
    apm_ids = {r["apmId"] for r in results}
    assert "apm-a" in apm_ids, "Should find apm-a"
    assert "apm-b" in apm_ids, "Should find apm-b"
    print("  PASS: fetch — multiple app directories")


def test_fetch_apmids_region_filter():
    """Region filter should narrow scan to that region only."""
    today = datetime.now(timezone.utc).strftime("%Y/%m/%d")

    mock_container = MagicMock()
    dir_a = MagicMock(); dir_a.name = f"{today}/app-a/"
    mock_container.walk_blobs.return_value = [dir_a]
    mock_container.list_blobs.return_value = []

    blob.fetch_apmids_from_blob(mock_container, days=1, region_filter="ftw")

    # Verify list_blobs was called with ftw only, not all 4 regions
    calls = mock_container.list_blobs.call_args_list
    prefixes_called = [c.kwargs.get("name_starts_with", c.args[0] if c.args else "") for c in calls]
    assert all("ftw" in p for p in prefixes_called), f"Should only scan ftw, got: {prefixes_called}"
    assert not any("wau" in p for p in prefixes_called), "Should NOT scan wau when filtered to ftw"
    print("  PASS: fetch — region filter limits scan")


def test_fetch_apmids_env_filter():
    """Env filter should narrow scan prefix."""
    today = datetime.now(timezone.utc).strftime("%Y/%m/%d")

    mock_container = MagicMock()
    dir_a = MagicMock(); dir_a.name = f"{today}/app-a/"
    mock_container.walk_blobs.return_value = [dir_a]
    mock_container.list_blobs.return_value = []

    blob.fetch_apmids_from_blob(mock_container, days=1, env_filter="prod")

    calls = mock_container.list_blobs.call_args_list
    prefixes_called = [c.kwargs.get("name_starts_with", "") for c in calls]
    assert all("prod" in p for p in prefixes_called), f"Should include prod in prefix, got: {prefixes_called}"
    print("  PASS: fetch — env filter in scan prefix")


def test_fetch_apmids_max_blobs():
    """max_blobs should stop processing early."""
    today = datetime.now(timezone.utc).strftime("%Y/%m/%d")

    events = [{"apmId": "app1", "appName": "App1"}]
    gz_data = make_gzipped_json_lines(events)

    blobs = []
    for i in range(10):
        b = MagicMock()
        b.name = f"{today}/testapp/ftw/prod/CriblOut-{i:04d}.json.gz"
        blobs.append(b)

    mock_container = MagicMock()
    dir_a = MagicMock(); dir_a.name = f"{today}/testapp/"
    mock_container.walk_blobs.return_value = [dir_a]
    mock_container.list_blobs.return_value = blobs

    mock_dl = MagicMock()
    mock_dl.readall.return_value = gz_data
    mock_container.download_blob.return_value = mock_dl

    blob.fetch_apmids_from_blob(mock_container, days=1, max_blobs=3)

    download_count = mock_container.download_blob.call_count
    assert download_count == 3, f"Expected 3 downloads (max_blobs=3), got {download_count}"
    print("  PASS: fetch — max_blobs stops early")


def test_fetch_apmids_dedup_highest_count():
    """Dedup should keep appName with highest event count per apmId."""
    today = datetime.now(timezone.utc).strftime("%Y/%m/%d")

    # Blob 1: apmId=shared, appName=NameA (1 event)
    events_1 = [{"apmId": "shared", "appName": "NameA"}]
    # Blob 2: apmId=shared, appName=NameB (2 events)
    events_2 = [{"apmId": "shared", "appName": "NameB"}, {"apmId": "shared", "appName": "NameB"}]

    blob_1 = MagicMock(); blob_1.name = f"{today}/testapp/ftw/prod/CriblOut-0001.json.gz"
    blob_2 = MagicMock(); blob_2.name = f"{today}/testapp/ftw/prod/CriblOut-0002.json.gz"

    mock_container = MagicMock()
    dir_a = MagicMock(); dir_a.name = f"{today}/testapp/"
    mock_container.walk_blobs.return_value = [dir_a]

    # Only return blobs for ftw region
    def list_blobs_side_effect(name_starts_with=""):
        if "/ftw/" in name_starts_with:
            return [blob_1, blob_2]
        return []
    mock_container.list_blobs.side_effect = list_blobs_side_effect

    def download_side_effect(name):
        mock_dl = MagicMock()
        if "0001" in name:
            mock_dl.readall.return_value = make_gzipped_json_lines(events_1)
        else:
            mock_dl.readall.return_value = make_gzipped_json_lines(events_2)
        return mock_dl
    mock_container.download_blob.side_effect = download_side_effect

    results = blob.fetch_apmids_from_blob(mock_container, days=1)
    assert len(results) == 1, f"Should dedup to 1 apmId, got {len(results)}"
    assert results[0]["appName"] == "NameB", "Should keep NameB (higher count)"
    assert results[0]["event_count"] == 2
    print("  PASS: fetch — dedup keeps highest count appName")


def test_fetch_apmids_blob_error_continues():
    """Errors on individual blobs should not stop processing."""
    today = datetime.now(timezone.utc).strftime("%Y/%m/%d")

    events = [{"apmId": "good-app", "appName": "GoodApp"}]
    gz_data = make_gzipped_json_lines(events)

    blob_bad = MagicMock(); blob_bad.name = f"{today}/testapp/ftw/prod/CriblOut-0001.json.gz"
    blob_good = MagicMock(); blob_good.name = f"{today}/testapp/ftw/prod/CriblOut-0002.json.gz"

    mock_container = MagicMock()
    dir_a = MagicMock(); dir_a.name = f"{today}/testapp/"
    mock_container.walk_blobs.return_value = [dir_a]
    mock_container.list_blobs.return_value = [blob_bad, blob_good]

    call_count = [0]
    def download_side_effect(name):
        call_count[0] += 1
        if call_count[0] == 1:
            raise ConnectionError("Simulated blob download failure")
        mock_dl = MagicMock()
        mock_dl.readall.return_value = gz_data
        return mock_dl
    mock_container.download_blob.side_effect = download_side_effect

    results = blob.fetch_apmids_from_blob(mock_container, days=1)
    assert len(results) == 1, "Should still get results from good blob"
    assert results[0]["apmId"] == "good-app"
    print("  PASS: fetch — blob error doesn't stop processing")


def test_fetch_apmids_no_apmid_in_json():
    """Lines without apmId should be skipped."""
    today = datetime.now(timezone.utc).strftime("%Y/%m/%d")

    events = [
        {"other_field": "value", "msg": "no apmId here"},
        {"apmId": "found-it", "appName": "FoundApp"},
    ]
    gz_data = make_gzipped_json_lines(events)

    mock_blob = MagicMock()
    mock_blob.name = f"{today}/testapp/ftw/prod/CriblOut-0001.json.gz"

    mock_container = MagicMock()
    dir_a = MagicMock(); dir_a.name = f"{today}/testapp/"
    mock_container.walk_blobs.return_value = [dir_a]
    mock_container.list_blobs.return_value = [mock_blob]
    mock_dl = MagicMock()
    mock_dl.readall.return_value = gz_data
    mock_container.download_blob.return_value = mock_dl

    results = blob.fetch_apmids_from_blob(mock_container, days=1)
    assert len(results) == 1
    assert results[0]["apmId"] == "found-it"
    print("  PASS: fetch — skips lines without apmId")


def test_fetch_appname_from_json_over_path():
    """appName from JSON should take priority over directory name."""
    today = datetime.now(timezone.utc).strftime("%Y/%m/%d")

    events = [{"apmId": "app1", "appName": "JsonAppName"}]
    gz_data = make_gzipped_json_lines(events)

    mock_blob = MagicMock()
    mock_blob.name = f"{today}/dir-app-name/ftw/prod/CriblOut-0001.json.gz"

    mock_container = MagicMock()
    dir_a = MagicMock(); dir_a.name = f"{today}/dir-app-name/"
    mock_container.walk_blobs.return_value = [dir_a]
    mock_container.list_blobs.return_value = [mock_blob]
    mock_dl = MagicMock()
    mock_dl.readall.return_value = gz_data
    mock_container.download_blob.return_value = mock_dl

    results = blob.fetch_apmids_from_blob(mock_container, days=1)
    assert results[0]["appName"] == "JsonAppName", "JSON appName should win over dir name"
    print("  PASS: fetch — appName from JSON takes priority over path")


def test_fetch_appname_fallback_to_path():
    """If appName is missing from JSON, fall back to directory name."""
    today = datetime.now(timezone.utc).strftime("%Y/%m/%d")

    events = [{"apmId": "app1"}]  # no appName
    gz_data = make_gzipped_json_lines(events)

    mock_blob = MagicMock()
    mock_blob.name = f"{today}/path-app-name/ftw/prod/CriblOut-0001.json.gz"

    mock_container = MagicMock()
    dir_a = MagicMock(); dir_a.name = f"{today}/path-app-name/"
    mock_container.walk_blobs.return_value = [dir_a]
    mock_container.list_blobs.return_value = [mock_blob]
    mock_dl = MagicMock()
    mock_dl.readall.return_value = gz_data
    mock_container.download_blob.return_value = mock_dl

    results = blob.fetch_apmids_from_blob(mock_container, days=1)
    assert results[0]["appName"] == "path-app-name", "Should fall back to dir name"
    print("  PASS: fetch — appName falls back to directory name")


# ---------------------------------------------------------------------------
# 5. Output functions
# ---------------------------------------------------------------------------

def test_save_csv(tmp_path=None):
    """Validate CSV output and missing-only CSV."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        outpath = os.path.join(tmpdir, "results.csv")
        results = [
            {"apmId": "a", "appName": "A", "event_count": 10,
             "has_destination": True, "destination_id": "dest-a",
             "has_route": True, "route_id": "route-a", "status": "CONFIGURED"},
            {"apmId": "b", "appName": "B", "event_count": 5,
             "has_destination": False, "destination_id": "NONE",
             "has_route": False, "route_id": "NONE", "status": "MISSING_BOTH"},
        ]
        blob.save_csv(results, outpath)

        # Verify main CSV
        assert os.path.exists(outpath), "CSV should be created"
        with open(outpath) as f:
            reader = list(csv.DictReader(f))
        assert len(reader) == 2
        assert reader[0]["apmId"] == "a"
        assert reader[1]["status"] == "MISSING_BOTH"

        # Verify missing-only CSV
        missing_path = os.path.join(tmpdir, "results_missing_only.csv")
        assert os.path.exists(missing_path), "Missing-only CSV should be created"
        with open(missing_path) as f:
            reader = list(csv.DictReader(f))
        assert len(reader) == 1
        assert reader[0]["apmId"] == "b"

    print("  PASS: save_csv — main + missing_only CSVs")

import csv  # needed for test_save_csv

def test_save_json():
    """Validate JSON output."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        outpath = os.path.join(tmpdir, "results.json")
        results = [{"apmId": "a", "appName": "A", "event_count": 10, "status": "CONFIGURED"}]
        blob.save_json(results, outpath)

        assert os.path.exists(outpath)
        with open(outpath) as f:
            data = json.load(f)
        assert len(data) == 1
        assert data[0]["apmId"] == "a"

    print("  PASS: save_json — JSON output valid")


# ---------------------------------------------------------------------------
# 6. Config / build_blob_client validation
# ---------------------------------------------------------------------------

def test_load_config_missing():
    """Missing config file should exit."""
    try:
        blob.load_config("/nonexistent/path.json")
        assert False, "Should have exited"
    except SystemExit as e:
        assert "not found" in str(e)
    print("  PASS: load_config — exits on missing file")

def test_load_config_valid():
    """Valid config should load."""
    import tempfile
    cfg = {"auth": {"cribl_url": "https://test.com"}, "blob_storage": {"connection_string": "x"}}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(cfg, f)
        f.flush()
        result = blob.load_config(f.name)
    os.unlink(f.name)
    assert result["auth"]["cribl_url"] == "https://test.com"
    print("  PASS: load_config — valid config loads")

def test_build_blob_client_no_creds():
    """No credentials should exit."""
    try:
        blob.build_blob_client({})
        assert False, "Should have exited"
    except SystemExit:
        pass
    print("  PASS: build_blob_client — exits with no credentials")


# ---------------------------------------------------------------------------
# 7. KNOWN_REGIONS constant
# ---------------------------------------------------------------------------

def test_known_regions():
    assert blob.KNOWN_REGIONS == ["wau", "ftw", "azn", "azs"]
    print("  PASS: KNOWN_REGIONS matches expected values")


# ---------------------------------------------------------------------------
# 8. print_status_table (no crash on edge cases)
# ---------------------------------------------------------------------------

def test_print_status_table_empty():
    blob.print_status_table([])
    print("  PASS: print_status_table — empty list no crash")

def test_print_status_table_single():
    results = [{
        "apmId": "test", "appName": "Test", "event_count": 1,
        "has_destination": False, "destination_id": "NONE",
        "has_route": False, "route_id": "NONE", "status": "MISSING_BOTH",
    }]
    blob.print_status_table(results)
    print("  PASS: print_status_table — single result")


# ---------------------------------------------------------------------------
# 9. discover_app_dirs
# ---------------------------------------------------------------------------

def test_discover_app_dirs():
    mock_container = MagicMock()
    dir1 = MagicMock(); dir1.name = "2026/06/17/app-one/"
    dir2 = MagicMock(); dir2.name = "2026/06/17/app-two/"
    mock_container.walk_blobs.return_value = [dir1, dir2]

    result = blob.discover_app_dirs(mock_container, "2026/06/17")
    assert result == ["app-one", "app-two"]
    mock_container.walk_blobs.assert_called_once_with(name_starts_with="2026/06/17/", delimiter="/")
    print("  PASS: discover_app_dirs — lists app directories")


# ---------------------------------------------------------------------------
# Run all tests
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("\n=== Deep Validation: get_apmids_from_blob.py ===\n")

    tests = [
        # parse_blob_path
        test_parse_blob_path_valid,
        test_parse_blob_path_too_short,
        test_parse_blob_path_wrong_filename,
        test_parse_blob_path_not_gzipped,
        test_parse_blob_path_deeper_nesting,
        # generate_date_prefixes
        test_generate_date_prefixes_today,
        test_generate_date_prefixes_multi,
        test_generate_date_prefixes_format,
        # check_route_dest_status
        test_matching_configured,
        test_matching_missing_both,
        test_matching_missing_route,
        test_matching_missing_destination,
        test_matching_case_insensitive,
        test_matching_empty_inputs,
        test_matching_multiple_apmids,
        # fetch_apmids_from_blob
        test_fetch_apmids_reads_first_2_lines_only,
        test_fetch_apmids_multiple_apps,
        test_fetch_apmids_region_filter,
        test_fetch_apmids_env_filter,
        test_fetch_apmids_max_blobs,
        test_fetch_apmids_dedup_highest_count,
        test_fetch_apmids_blob_error_continues,
        test_fetch_apmids_no_apmid_in_json,
        test_fetch_appname_from_json_over_path,
        test_fetch_appname_fallback_to_path,
        # Output
        test_save_csv,
        test_save_json,
        # Config
        test_load_config_missing,
        test_load_config_valid,
        test_build_blob_client_no_creds,
        # Constants
        test_known_regions,
        # Edge cases
        test_print_status_table_empty,
        test_print_status_table_single,
        test_discover_app_dirs,
    ]

    passed = 0
    failed = 0
    for test_fn in tests:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"  FAIL: {test_fn.__name__} — {e}")

    print(f"\n{'='*50}")
    print(f"  Results: {passed} passed, {failed} failed out of {len(tests)}")
    print(f"{'='*50}\n")

    sys.exit(1 if failed else 0)
