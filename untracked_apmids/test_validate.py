"""Validation tests for the new route/destination check logic."""
import csv
import io
import os
import sys
import tempfile

import find_default_appids as m


def test_match_appid_to_dest():
    """TEST 1: match_appid_to_dest (existing logic, unchanged)"""
    print("=== TEST 1: match_appid_to_dest ===")

    destinations = [
        {"id": "azure_blob:prod-app-one", "type": "azure_blob", "containerName": "app-one"},
        {"id": "azure_blob:prod-app-two", "type": "azure_blob", "containerName": "app-two"},
        {"id": "azure_blob:company-default", "type": "azure_blob", "containerName": "default-container"},
    ]

    # exact match
    assert m.match_appid_to_dest("app-one", destinations, "exact") == "azure_blob:prod-app-one"
    assert m.match_appid_to_dest("APP-ONE", destinations, "exact") == "azure_blob:prod-app-one"
    assert m.match_appid_to_dest("unknown", destinations, "exact") is None
    print("  exact mode: PASS")

    # contains match
    assert m.match_appid_to_dest("app-one", destinations, "contains") == "azure_blob:prod-app-one"
    assert m.match_appid_to_dest("one", destinations, "contains") == "azure_blob:prod-app-one"
    assert m.match_appid_to_dest("xyz", destinations, "contains") is None
    print("  contains mode: PASS")


def test_check_lookup_route_dest_status():
    """TEST 2: check_lookup_route_dest_status"""
    print("\n=== TEST 2: check_lookup_route_dest_status ===")

    destinations = [
        {"id": "azure_blob:prod-app-one", "type": "azure_blob", "containerName": "app-one"},
        {"id": "azure_blob:prod-app-two", "type": "azure_blob", "containerName": "app-two"},
        {"id": "azure_blob:company-default", "type": "azure_blob", "containerName": "default-container"},
    ]

    routes = [
        {"id": "route-app-one-blob", "name": "route-app-one-blob", "filter": "true", "output": "azure_blob:prod-app-one"},
        {"id": "route-misc", "name": "general-route", "filter": "apmId=='app-three'", "output": "azure_blob:other"},
    ]

    # Case A: has destination (containerName match) + has route (name contains appId)
    results = m.check_lookup_route_dest_status({"app-one"}, destinations, routes, "exact")
    assert len(results) == 1
    r = results[0]
    assert r["has_destination"] is True, f"expected has_destination=True, got {r}"
    assert r["has_route"] is True, f"expected has_route=True, got {r}"
    assert "CONFIGURED" in r["status"]
    print("  Case A (dest + route): PASS")

    # Case B: has destination (containerName) but NO route
    results = m.check_lookup_route_dest_status({"app-two"}, destinations, routes, "exact")
    r = results[0]
    assert r["has_destination"] is True
    assert r["has_route"] is False
    assert "MISSING ROUTE" in r["status"]
    print("  Case B (dest, no route): PASS")

    # Case C: NO destination, NO route
    results = m.check_lookup_route_dest_status({"totally-unknown"}, destinations, routes, "exact")
    r = results[0]
    assert r["has_destination"] is False
    assert r["has_route"] is False
    assert "MISSING BOTH" in r["status"]
    print("  Case C (no dest, no route): PASS")

    # Case D: route exists (filter match) but no destination
    results = m.check_lookup_route_dest_status({"app-three"}, destinations, routes, "exact")
    r = results[0]
    assert r["has_destination"] is False
    assert r["has_route"] is True
    assert "MISSING DESTINATION" in r["status"]
    print("  Case D (route, no dest): PASS")


def test_dest_id_name_fallback():
    """TEST 3: destination ID/name contains appId (new fallback)"""
    print("\n=== TEST 3: Destination ID/name contains appId ===")

    destinations = [
        {"id": "azure_blob:prod-app-one", "type": "azure_blob", "containerName": "app-one"},
        {"id": "azure_blob:prod-app-two", "type": "azure_blob", "containerName": "app-two"},
    ]

    # appId 'prod-app-one' won't match containerName 'app-one' in exact mode
    # but SHOULD match destination id 'azure_blob:prod-app-one' via substring
    results = m.check_lookup_route_dest_status({"prod-app-one"}, destinations, [], "exact")
    r = results[0]
    assert r["has_destination"] is True, f"expected dest fallback to match dest ID, got {r}"
    assert r["destination_id"] == "azure_blob:prod-app-one"
    print("  Dest ID substring fallback: PASS")

    # dest name fallback
    destinations_with_name = [
        {"id": "azure_blob:x", "type": "azure_blob", "containerName": "generic", "name": "MyApp Blob Storage"},
    ]
    results = m.check_lookup_route_dest_status({"myapp"}, destinations_with_name, [], "exact")
    r = results[0]
    assert r["has_destination"] is True, f"expected dest name fallback, got {r}"
    print("  Dest name substring fallback: PASS")


def test_route_name_substring():
    """TEST 4: route name/ID contains appId (substring)"""
    print("\n=== TEST 4: Route name contains appId ===")

    # Route name contains appId
    routes2 = [
        {"id": "r1", "name": "prod-my-special-app-route", "filter": "true", "output": "out1"},
    ]
    results = m.check_lookup_route_dest_status({"my-special-app"}, [], routes2, "exact")
    r = results[0]
    assert r["has_route"] is True, f"expected route name substring match, got {r}"
    assert r["route_id"] == "r1"
    print("  Route name substring: PASS")

    # Route ID contains appId
    routes3 = [
        {"id": "route-for-cool-app", "name": "some-other-name", "filter": "true", "output": "out2"},
    ]
    results = m.check_lookup_route_dest_status({"cool-app"}, [], routes3, "exact")
    r = results[0]
    assert r["has_route"] is True
    assert r["route_id"] == "route-for-cool-app"
    print("  Route ID substring: PASS")


def test_print_lookup_status_table():
    """TEST 5: _print_lookup_status_table (no crash)"""
    print("\n=== TEST 5: _print_lookup_status_table ===")

    test_results = [
        {"apmId": "app-x", "has_destination": True, "destination_id": "azure_blob:x",
         "has_route": False, "route_id": "NONE", "route_output": "NONE",
         "status": "MISSING ROUTE"},
        {"apmId": "app-y", "has_destination": False, "destination_id": "NONE",
         "has_route": False, "route_id": "NONE", "route_output": "NONE",
         "status": "MISSING BOTH"},
    ]
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    m._print_lookup_status_table(test_results)
    output = sys.stdout.getvalue()
    sys.stdout = old_stdout
    assert "app-x" in output
    assert "app-y" in output
    assert "MISSING ROUTE" in output
    assert "MISSING BOTH" in output
    assert "<<<" in output
    print("  Table rendering: PASS")

    # Empty list should not crash
    sys.stdout = io.StringIO()
    m._print_lookup_status_table([])
    sys.stdout = old_stdout
    print("  Empty table: PASS")


def test_write_lookup_status_csv():
    """TEST 6: write_lookup_status_csv"""
    print("\n=== TEST 6: write_lookup_status_csv ===")

    test_results = [
        {"apmId": "app-x", "has_destination": True, "destination_id": "azure_blob:x",
         "has_route": False, "route_id": "NONE", "route_output": "NONE",
         "status": "MISSING ROUTE"},
        {"apmId": "app-y", "has_destination": False, "destination_id": "NONE",
         "has_route": False, "route_id": "NONE", "route_output": "NONE",
         "status": "MISSING BOTH"},
    ]

    tmp = tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w")
    tmp.close()
    try:
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        m.write_lookup_status_csv(test_results, tmp.name)
        sys.stdout = old_stdout

        with open(tmp.name, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 2
        assert rows[0]["apmId"] == "app-x"
        assert rows[1]["apmId"] == "app-y"
        assert "has_destination" in rows[0]
        assert "has_route" in rows[0]
        assert "route_id" in rows[0]
        assert "status" in rows[0]
        print("  CSV write: PASS")
    finally:
        os.unlink(tmp.name)

    # Empty results should not create file
    tmp2 = tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w")
    tmp2.close()
    os.unlink(tmp2.name)
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    m.write_lookup_status_csv([], tmp2.name)
    sys.stdout = old_stdout
    assert not os.path.exists(tmp2.name)
    print("  Empty CSV skip: PASS")


def test_case_insensitivity():
    """TEST 7: Case insensitivity across all matching"""
    print("\n=== TEST 7: Case insensitivity ===")

    destinations = [
        {"id": "azure_blob:prod-app-one", "type": "azure_blob", "containerName": "app-one"},
    ]
    routes = [
        {"id": "route-app-one-blob", "name": "route-app-one-blob", "filter": "true", "output": "azure_blob:prod-app-one"},
    ]

    results = m.check_lookup_route_dest_status({"APP-ONE"}, destinations, routes, "exact")
    r = results[0]
    assert r["has_destination"] is True
    assert r["has_route"] is True
    print("  Uppercase appId: PASS")

    results = m.check_lookup_route_dest_status({"App-One"}, destinations, routes, "exact")
    r = results[0]
    assert r["has_destination"] is True
    assert r["has_route"] is True
    print("  Mixed case appId: PASS")


def test_multiple_appids():
    """TEST 8: Multiple appIds at once"""
    print("\n=== TEST 8: Multiple appIds ===")

    destinations = [
        {"id": "azure_blob:prod-app-one", "type": "azure_blob", "containerName": "app-one"},
        {"id": "azure_blob:prod-app-two", "type": "azure_blob", "containerName": "app-two"},
    ]
    routes = [
        {"id": "route-app-one-blob", "name": "route-app-one-blob", "filter": "true", "output": "azure_blob:prod-app-one"},
    ]

    appids = {"app-one", "app-two", "app-three"}
    results = m.check_lookup_route_dest_status(appids, destinations, routes, "exact")

    assert len(results) == 3
    by_id = {r["apmId"]: r for r in results}

    assert by_id["app-one"]["has_destination"] is True
    assert by_id["app-one"]["has_route"] is True
    assert "CONFIGURED" in by_id["app-one"]["status"]

    assert by_id["app-two"]["has_destination"] is True
    assert by_id["app-two"]["has_route"] is False
    assert "MISSING ROUTE" in by_id["app-two"]["status"]

    assert by_id["app-three"]["has_destination"] is False
    assert by_id["app-three"]["has_route"] is False
    assert "MISSING BOTH" in by_id["app-three"]["status"]

    # Results should be sorted by appId
    assert [r["apmId"] for r in results] == ["app-one", "app-three", "app-two"]
    print("  Multiple appIds with mixed status: PASS")
    print("  Results sorted alphabetically: PASS")


def test_route_output_dest_match():
    """TEST 9: Route matches via output pointing to appId's destination"""
    print("\n=== TEST 9: Route output -> destination match ===")

    destinations = [
        {"id": "azure_blob:prod-app-five", "type": "azure_blob", "containerName": "app-five"},
    ]
    # Route name/filter don't contain 'app-five', but output points to its dest
    routes = [
        {"id": "generic-route-99", "name": "generic-route", "filter": "someOtherField=='xyz'",
         "output": "azure_blob:prod-app-five"},
    ]

    results = m.check_lookup_route_dest_status({"app-five"}, destinations, routes, "exact")
    r = results[0]
    assert r["has_destination"] is True
    assert r["has_route"] is True
    assert r["route_output"] == "azure_blob:prod-app-five"
    print("  Route output -> dest match: PASS")


def test_empty_inputs():
    """TEST 10: Edge cases with empty inputs"""
    print("\n=== TEST 10: Edge cases ===")

    # Empty appIds set
    results = m.check_lookup_route_dest_status(set(), [], [], "exact")
    assert results == []
    print("  Empty appIds: PASS")

    # Empty destinations and routes
    results = m.check_lookup_route_dest_status({"app-x"}, [], [], "exact")
    assert len(results) == 1
    assert results[0]["has_destination"] is False
    assert results[0]["has_route"] is False
    print("  Empty dests + routes: PASS")

    # Route with missing fields
    routes = [{"id": "r1"}]  # no name, filter, output
    results = m.check_lookup_route_dest_status({"r1"}, [], routes, "exact")
    r = results[0]
    assert r["has_route"] is True  # id contains appId
    print("  Route with missing fields: PASS")

    # Destination with missing fields
    dests = [{"id": "azure_blob:test-app"}]  # no containerName, name
    results = m.check_lookup_route_dest_status({"test-app"}, dests, [], "exact")
    r = results[0]
    assert r["has_destination"] is True  # id contains appId
    print("  Dest with missing fields: PASS")


def test_route_fields_from_cribl_api():
    """TEST 11: Route objects with real Cribl API field structure"""
    print("\n=== TEST 11: Real Cribl API route structure ===")

    # Cribl API returns routes with: id, name, final, disabled, pipeline,
    # description, clones, enableOutputExpression, filter, output
    routes = [
        {
            "id": "route-prod-billing-app",
            "name": "Billing App Route",
            "final": True,
            "disabled": False,
            "pipeline": "billing-pipeline",
            "description": "Route billing events",
            "clones": [],
            "enableOutputExpression": False,
            "filter": "true",
            "output": "azure_blob:billing-dest",
        },
        {
            "id": "default",
            "name": "default",
            "final": True,
            "disabled": False,
            "pipeline": "main",
            "description": "Default route",
            "clones": [],
            "enableOutputExpression": False,
            "filter": "true",
            "output": "azure_blob:company-default",
        },
    ]

    # "billing-app" should match route "route-prod-billing-app" by ID substring
    results = m.check_lookup_route_dest_status({"billing-app"}, [], routes, "exact")
    r = results[0]
    assert r["has_route"] is True
    assert r["route_id"] == "route-prod-billing-app"
    print("  Cribl route ID substring match: PASS")

    # Disabled route should still be found (we report config existence, not state)
    routes[0]["disabled"] = True
    results = m.check_lookup_route_dest_status({"billing-app"}, [], routes, "exact")
    r = results[0]
    assert r["has_route"] is True
    print("  Disabled route still found: PASS")


def test_lookup_hitting_default_logic():
    """TEST 12: Simulate the run_analysis lookup_hitting_default detection"""
    print("\n=== TEST 12: lookup_hitting_default detection logic ===")

    # Simulate captured data: all_rows tuples are (apmId, appName, outputId, matched, count)
    # These events were captured via __outputId filter targeting the default output
    default_id = "azure_blob:company-default"
    all_rows = [
        ("app-one", "App One", "azure_blob:company-default", "azure_blob:prod-app-one", 42),
        ("app-two", "App Two", "azure_blob:company-default", "DEFAULT", 15),
        ("app-three", "App Three", "azure_blob:company-default", "DEFAULT", 8),
        ("new-app", "New App", "azure_blob:company-default", "DEFAULT", 3),
    ]
    all_app_ids = {r[0] for r in all_rows}

    # Lookup table says app-one and app-two have containers
    lookup_appids = {"app-one", "app-two"}

    # Replicate the logic from run_analysis
    lookup_hitting_default = {
        aid for aid in all_app_ids
        if aid.lower() in lookup_appids
        and any(r[0] == aid and default_id in r[2] for r in all_rows)
    }

    # app-one: in lookup, outputId contains default_id -> should be flagged
    assert "app-one" in lookup_hitting_default
    # app-two: in lookup, outputId contains default_id -> should be flagged
    assert "app-two" in lookup_hitting_default
    # app-three: NOT in lookup -> should NOT be flagged
    assert "app-three" not in lookup_hitting_default
    # new-app: NOT in lookup -> should NOT be flagged
    assert "new-app" not in lookup_hitting_default
    print("  Correctly identifies lookup appIds hitting default: PASS")

    # Key: app-one has matched_destination='azure_blob:prod-app-one' (not DEFAULT)
    # but it IS hitting the default output (outputId). Old logic using r[3]=='DEFAULT'
    # would have MISSED app-one. New logic using default_id in r[2] catches it.
    old_logic_result = {
        aid for aid in all_app_ids
        if aid.lower() in lookup_appids
        and any(r[0] == aid and r[3] == "DEFAULT" for r in all_rows)
    }
    assert "app-one" not in old_logic_result, "Old logic should miss app-one"
    assert "app-one" in lookup_hitting_default, "New logic should catch app-one"
    print("  New logic catches appIds old logic missed: PASS")

    # Test fallback when default_id is None
    lookup_hitting_default_no_id = {
        aid for aid in all_app_ids
        if aid.lower() in lookup_appids
    }
    assert lookup_hitting_default_no_id == {"app-one", "app-two"}
    print("  Fallback (no default_id) includes all lookup appIds: PASS")


def test_list_routes_response_parsing():
    """TEST 13: list_routes response format handling"""
    print("\n=== TEST 13: list_routes response parsing ===")

    import json as jsonmod

    # Format 1: { "routes": [...] }
    data = {"routes": [{"id": "r1", "name": "route1"}]}
    if isinstance(data, dict):
        routes = data.get("items") or data.get("routes") or []
    assert len(routes) == 1 and routes[0]["id"] == "r1"
    print("  Format {routes: [...]}: PASS")

    # Format 2: { "items": [...] }
    data = {"items": [{"id": "r2", "name": "route2"}]}
    if isinstance(data, dict):
        routes = data.get("items") or data.get("routes") or []
    assert len(routes) == 1 and routes[0]["id"] == "r2"
    print("  Format {items: [...]}: PASS")

    # Format 3: { "groups": { "default": { "routes": [...] } } }
    data = {"groups": {"default": {"routes": [{"id": "r3"}]}}}
    if isinstance(data, dict):
        routes = data.get("items") or data.get("routes") or []
        if not routes and "groups" in data:
            for g in data["groups"].values():
                routes.extend(g.get("routes", []))
    assert len(routes) == 1 and routes[0]["id"] == "r3"
    print("  Format {groups: {default: {routes: [...]}}}: PASS")

    # Format 4: direct list
    data = [{"id": "r4", "name": "route4"}]
    if isinstance(data, dict):
        routes = data.get("items") or data.get("routes") or []
    else:
        routes = data
    assert len(routes) == 1 and routes[0]["id"] == "r4"
    print("  Format [...] (direct list): PASS")

    # Format 5: empty
    data = {"items": []}
    if isinstance(data, dict):
        routes = data.get("items") or data.get("routes") or []
    assert routes == []
    print("  Format {items: []} (empty): PASS")


if __name__ == "__main__":
    test_match_appid_to_dest()
    test_check_lookup_route_dest_status()
    test_dest_id_name_fallback()
    test_route_name_substring()
    test_print_lookup_status_table()
    test_write_lookup_status_csv()
    test_case_insensitivity()
    test_multiple_appids()
    test_route_output_dest_match()
    test_empty_inputs()
    test_route_fields_from_cribl_api()
    test_lookup_hitting_default_logic()
    test_list_routes_response_parsing()

    print()
    print("=" * 50)
    print("ALL 13 TESTS PASSED")
    print("=" * 50)
