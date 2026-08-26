"""Unit tests for the AI League Lambda tools.

Run from the tools directory:  python -m pytest tests -q
Worked examples are taken verbatim from game-data.md.
"""

import hashlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import claims_eob_solver  # noqa: E402
import code_executor  # noqa: E402
import course_optimizer  # noqa: E402
import map_analyzer  # noqa: E402
import pathfinder  # noqa: E402
import permit_calculator  # noqa: E402
import registry_lookup  # noqa: E402


# ---------------------------------------------------------------------------
# permit_calculator (c18)
# ---------------------------------------------------------------------------


def test_permit_worked_example():
    answer = permit_calculator.build_answer(100, "solar")
    assert answer == (
        '{"annual_generation_gwh":219.0,"carbon_offset_tons":91980,"homes_powered":20857}'
    )


def test_permit_parses_description():
    body = {"text": "Calculate the energy impact of a 100 MW solar farm in California."}
    response = permit_calculator.lambda_handler(body, None)
    answer = json.loads(response["body"])["answer"]
    assert json.loads(answer)["annual_generation_gwh"] == 219.0


def test_permit_detects_offshore_before_wind():
    assert permit_calculator.detect_kind("a 200 MW offshore wind project") == "offshore_wind"
    assert permit_calculator.detect_kind("a 200 MW onshore wind farm") == "onshore_wind"
    assert permit_calculator.detect_kind("a 50 MW wind farm") == "onshore_wind"


def test_permit_offshore_maths():
    gwh, carbon, homes = permit_calculator.compute(100, "offshore_wind")
    assert gwh == pytest.approx(394.2)
    assert carbon == int(394.2 * 420)
    assert homes == int(394.2 * 1_000_000 / 10_500)


# ---------------------------------------------------------------------------
# claims_eob_solver (c3)
# ---------------------------------------------------------------------------

FHIR_ITEM = [{
    "sequence": 1,
    "productOrService": {"coding": [{"code": "99213", "display": "Office visit"}]},
    "adjudication": [
        {"category": {"coding": [{"code": "submitted"}]}, "amount": {"value": 150.00}},
        {"category": {"coding": [{"code": "eligible"}]}, "amount": {"value": 120.00}},
        {"category": {"coding": [{"code": "copay"}]}, "amount": {"value": 25.00}},
        {"category": {"coding": [{"code": "deductible"}]}, "amount": {"value": 0.00}},
        {"category": {"coding": [{"code": "benefit"}]}, "amount": {"value": 71.00}},
    ],
}]


def test_claims_worked_example_from_fhir():
    answer = claims_eob_solver.build_answer(FHIR_ITEM)
    assert answer == (
        '{"TotalAllowed":120.00,"MemberResponsibility":49.00,"DeniedLines":[]}'
    )


def test_claims_compact_rows_match_fhir():
    legacy_rows = [["99213", 150.00, 120.00, 25.00, 0.00, 71.00, 0, ""]]
    pipe_rows = "99213|150|120|71|0|"
    expected = claims_eob_solver.build_answer(FHIR_ITEM)
    assert claims_eob_solver.build_answer(legacy_rows) == expected
    assert claims_eob_solver.build_answer(pipe_rows) == expected


def test_claims_denied_line_owes_submitted_and_reports_carc():
    rows = [
        ["99213", 150.00, 120.00, 25.00, 0.00, 71.00, 0, ""],
        ["99214", 200.00, 160.00, 0.00, 0.00, 0.00, 1, "197"],
    ]
    answer = claims_eob_solver.build_answer(rows)
    parsed = json.loads(answer)
    assert parsed["TotalAllowed"] == 120.00           # denied line excluded
    assert parsed["MemberResponsibility"] == 249.00   # 49 + full 200 submitted
    assert parsed["DeniedLines"] == [{"code": "99214", "carc": "197"}]


def test_claims_money_always_two_decimals():
    rows = [["99213", 100.00, 100.00, 0.00, 0.00, 100.00, 0, ""]]
    answer = claims_eob_solver.build_answer(rows)
    assert '"MemberResponsibility":0.00' in answer
    assert '"TotalAllowed":100.00' in answer


def test_claims_denied_lines_keep_input_order():
    rows = [
        ["A1", 10.0, 10.0, 0, 0, 0, 1, "50"],
        ["B2", 20.0, 20.0, 0, 0, 20.0, 0, ""],
        ["C3", 30.0, 30.0, 0, 0, 0, 1, "18"],
    ]
    parsed = json.loads(claims_eob_solver.build_answer(rows))
    assert [line["code"] for line in parsed["DeniedLines"]] == ["A1", "C3"]


def _fhir_eob_wrapper(rows):
    """Build the relevant full EOB shape used by the 2026-08-26 challenges."""
    items = []
    for sequence, row in enumerate(rows, 1):
        code, submitted, eligible, copay, deductible, benefit, denied, carc = row
        item = {
            "sequence": sequence,
            "productOrService": {"coding": [{"code": code}]},
            "adjudication": [
                {
                    "category": {"coding": [{"code": category}]},
                    "amount": {"value": amount, "currency": "USD"},
                }
                for category, amount in (
                    ("submitted", submitted),
                    ("eligible", eligible),
                    ("copay", copay),
                    ("deductible", deductible),
                    ("benefit", benefit),
                )
            ],
        }
        if denied:
            item["reviewOutcome"] = {
                "decision": {"coding": [{"code": "denied"}]},
                "reason": {"coding": [{"code": carc}]},
            }
        items.append(item)
    return {"resourceType": "ExplanationOfBenefit", "item": items}


@pytest.mark.parametrize(
    ("legacy_rows", "pipe_rows", "expected"),
    [
        pytest.param(
            [
                ["99213", 150.0, 120.0, 25.0, 0.0, 71.0, 0, ""],
                ["90834", 160.0, 130.0, 25.0, 0.0, 79.0, 0, ""],
                ["90837", 200.0, 165.0, 0.0, 0.0, 0.0, 1, "119"],
                ["96127", 25.0, 20.0, 0.0, 0.0, 16.0, 0, ""],
                ["90846", 180.0, 145.0, 0.0, 0.0, 0.0, 1, "50"],
            ],
            (
                "99213|150|120|71|0|;90834|160|130|79|0|;"
                "90837|200|165|0|1|119;96127|25|20|16|0|;"
                "90846|180|145|0|1|50"
            ),
            (
                '{"TotalAllowed":270.00,"MemberResponsibility":484.00,'
                '"DeniedLines":[{"code":"90837","carc":"119"},'
                '{"code":"90846","carc":"50"}]}'
            ),
            id="failed-first-c3",
        ),
        pytest.param(
            [
                ["99213", 150.0, 120.0, 25.0, 0.0, 71.0, 0, ""],
                ["E0601", 1800.0, 1400.0, 0.0, 0.0, 0.0, 1, "197"],
                ["94010", 120.0, 95.0, 0.0, 0.0, 76.0, 0, ""],
                ["94729", 85.0, 68.0, 0.0, 0.0, 0.0, 1, "151"],
            ],
            (
                "99213|150|120|71|0|;E0601|1800|1400|0|1|197;"
                "94010|120|95|76|0|;94729|85|68|0|1|151"
            ),
            (
                '{"TotalAllowed":215.00,"MemberResponsibility":1953.00,'
                '"DeniedLines":[{"code":"E0601","carc":"197"},'
                '{"code":"94729","carc":"151"}]}'
            ),
            id="retried-second-c3",
        ),
    ],
)
def test_claims_logged_recovery_eobs_match_pipe_legacy_and_raw_fhir(
    legacy_rows, pipe_rows, expected
):
    eob = _fhir_eob_wrapper(legacy_rows)
    assert claims_eob_solver.build_answer(legacy_rows) == expected
    assert claims_eob_solver.build_answer(eob) == expected
    assert claims_eob_solver.build_answer(json.dumps(eob)) == expected
    assert claims_eob_solver.build_answer(pipe_rows) == expected

    response = claims_eob_solver.lambda_handler({
        "parameters": [{"name": "rows", "value": pipe_rows}]
    }, None)
    assert response["statusCode"] == 200
    answer = json.loads(response["body"])["answer"]
    assert answer == expected
    assert '"Benefits"' not in answer


def test_claims_pipe_rows_preserve_decimals_and_denied_order():
    rows = (
        "A1|10.25|8.75|3.10|0|;"
        "B2|4.55|3.00|0|1|197;"
        "C3|6.05|5.25|1.20|1|50"
    )
    assert claims_eob_solver.build_answer(rows) == (
        '{"TotalAllowed":8.75,"MemberResponsibility":16.25,'
        '"DeniedLines":[{"code":"B2","carc":"197"},'
        '{"code":"C3","carc":"50"}]}'
    )


def test_claims_generated_schema_keeps_rows_as_plain_string():
    import inspect

    os.environ.setdefault("AGENT_CONFIGURATIONS_TABLE", "SchemaOnly")
    os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
    agent_api = os.path.abspath(os.path.join(
        os.path.dirname(__file__), "..", "..", "lambda", "agentic-api"
    ))
    if agent_api not in sys.path:
        sys.path.insert(0, agent_api)
    import agent_config_handlers

    schemas = agent_config_handlers._try_parse_docstring_schema(
        inspect.getsource(claims_eob_solver)
    )
    assert len(schemas) == 1
    schema = schemas[0]
    assert schema["name"] == "c3"
    assert schema["inputSchema"]["required"] == ["rows"]
    rows = schema["inputSchema"]["properties"]["rows"]
    assert rows["type"] == "string"
    assert rows["description"] == (
        "semicolon rows code|submitted|eligible|benefit|denied0or1|carc"
    )


# ---------------------------------------------------------------------------
# course_optimizer (c2)
# ---------------------------------------------------------------------------


def test_course_worked_example():
    sections = [
        {"sectionId": "SEC-101", "courseName": "BIO-201", "instructor": "Dr. Smith",
         "timeSlot": "MWF 9:00AM", "enrolled": 12, "capacity": 35},
        {"sectionId": "SEC-102", "courseName": "BIO-201", "instructor": "Dr. Smith",
         "timeSlot": "MWF 11:00AM", "enrolled": 14, "capacity": 35},
    ]
    assert course_optimizer.build_answer(sections) == (
        '{"FlaggedSections":["SEC-101","SEC-102"],'
        '"Consolidations":[{"keep":"SEC-102","cancel":"SEC-101",'
        '"combinedEnrollment":26,"capacity":35}],"NoAction":[]}'
    )


def test_course_exactly_fifty_percent_is_not_flagged():
    sections = [{"sectionId": "S1", "courseName": "X", "instructor": "I",
                 "timeSlot": "T", "enrolled": 20, "capacity": 40}]
    assert course_optimizer.analyse(sections)["FlaggedSections"] == []


def test_course_same_timeslot_cannot_consolidate():
    sections = [
        {"sectionId": "S1", "courseName": "X", "instructor": "I",
         "timeSlot": "MWF 9AM", "enrolled": 10, "capacity": 40},
        {"sectionId": "S2", "courseName": "X", "instructor": "I",
         "timeSlot": "MWF 9AM", "enrolled": 12, "capacity": 40},
    ]
    result = course_optimizer.analyse(sections)
    assert result["Consolidations"] == []
    assert result["NoAction"] == ["S1", "S2"]


def test_course_unflagged_partner_leaves_the_flagged_section_alone():
    # S2 sits at 70% so it is not flagged and cannot absorb S1.
    sections = [
        {"sectionId": "S1", "courseName": "X", "instructor": "I",
         "timeSlot": "A", "enrolled": 14, "capacity": 30},
        {"sectionId": "S2", "courseName": "X", "instructor": "I",
         "timeSlot": "B", "enrolled": 14, "capacity": 20},
    ]
    result = course_optimizer.analyse(sections)
    assert result["FlaggedSections"] == ["S1"]
    assert result["Consolidations"] == []
    assert result["NoAction"] == ["S1"]


def test_course_capacity_rule_is_unreachable_by_construction():
    # Both flagged means each enrollment is under half its capacity, so the
    # combined total is always within the kept section's capacity. Documented
    # here so a future change to the flag threshold trips this test.
    sections = [
        {"sectionId": "S1", "courseName": "X", "instructor": "I",
         "timeSlot": "A", "enrolled": 40, "capacity": 100},
        {"sectionId": "S2", "courseName": "X", "instructor": "I",
         "timeSlot": "B", "enrolled": 9, "capacity": 20},
    ]
    result = course_optimizer.analyse(sections)
    assert result["Consolidations"] == [
        {"keep": "S1", "cancel": "S2", "combinedEnrollment": 49, "capacity": 100}
    ]
    assert result["NoAction"] == []


def test_course_different_courses_are_no_action():
    sections = [
        {"sectionId": "S1", "courseName": "X", "instructor": "I",
         "timeSlot": "A", "enrolled": 5, "capacity": 40},
        {"sectionId": "S2", "courseName": "Y", "instructor": "I",
         "timeSlot": "B", "enrolled": 5, "capacity": 40},
    ]
    result = course_optimizer.analyse(sections)
    assert result["FlaggedSections"] == ["S1", "S2"]
    assert result["NoAction"] == ["S1", "S2"]


def test_course_empty_input():
    assert course_optimizer.build_answer([]) == (
        '{"FlaggedSections":[],"Consolidations":[],"NoAction":[]}'
    )


# ---------------------------------------------------------------------------
# map_analyzer (c30 / c40)
# ---------------------------------------------------------------------------


def test_number_to_words():
    assert map_analyzer.number_to_words(6) == "SIX"
    assert map_analyzer.number_to_words(0) == "ZERO"
    assert map_analyzer.number_to_words(13) == "THIRTEEN"
    assert map_analyzer.number_to_words(20) == "TWENTY"
    assert map_analyzer.number_to_words(21) == "TWENTY-ONE"


def test_pass_string_matches_documented_format():
    assert map_analyzer.build_pass_string("x", 6) == "X-SIX"


def test_count_tile_is_configurable():
    grid = [["c5", "c5", "c6"], ["c5", "normal", "treasure"]]
    # phase="door" is the direct-diagnostic shape: ask for the pass string itself.
    body = {"game_map": grid, "count_tile": "c5", "key_char": "Q", "phase": "door"}
    # The default response is deliberately just {"answer": ...} to save tokens.
    assert json.loads(map_analyzer.lambda_handler(body, None)["body"]) == {
        "answer": "Q-THREE"
    }
    verbose = json.loads(
        map_analyzer.lambda_handler({**body, "verbose": True}, None)["body"]
    )
    assert verbose["count"] == 3
    assert verbose["count_source"] == "game_map"

    body["count_tile"] = "c6"
    result = json.loads(map_analyzer.lambda_handler(body, None)["body"])
    assert result["answer"] == "Q-ONE"


def test_door_is_the_default_answer_because_its_failure_is_the_expensive_one():
    """A wrong key costs 50 at zero damage; a wrong door costs 1000 and the run.

    So an omitted `phase` must resolve to the door pass string, never to the key
    reply. The key turn answers with the literal `Thanks` and no tool call.
    """
    door = json.loads(map_analyzer.lambda_handler(
        {"key_char": "P", "count_word": "FOUR"}, None
    )["body"])
    assert door == {"answer": "P-FOUR"}

    explicit = json.loads(map_analyzer.lambda_handler(
        {"key_char": "P", "count_word": "FOUR", "phase": "door"}, None
    )["body"])
    assert explicit == {"answer": "P-FOUR"}

    # The key reply stays available for direct diagnostics.
    prepared = json.loads(map_analyzer.lambda_handler(
        {"key_char": "P", "count_word": "FOUR", "phase": "prepare"}, None
    )["body"])
    assert prepared["answer"] == "Thanks P-FOUR"
    assert prepared["door_answer"] == "P-FOUR"


def test_map_overrides_a_hallucinated_count_and_rejects_path_lengths():
    """Two live runs lost the Red Door by sending the path length as the count."""
    grid = [["c5", "c5"], ["c5", "c5"]]
    both = json.loads(map_analyzer.lambda_handler(
        {"game_map": grid, "key_char": "P", "count_word": "NINETY-SIX", "phase": "door"}, None
    )["body"])
    assert both["answer"] == "P-FOUR"

    for bogus in ("EIGHTY", "NINETY-SIX"):
        rejected = json.loads(map_analyzer.lambda_handler(
            {"key_char": "P", "count_word": bogus}, None
        )["body"])
        assert "error" in rejected
        assert "game_map" in rejected["error"]

    assert map_analyzer.words_to_number("NINETY-SIX") == 96
    assert map_analyzer.words_to_number("FOUR") == 4
    assert map_analyzer.words_to_number("NOTANUMBER") is None


def test_loot_sealed_behind_a_locked_door_is_collected_after_unlocking():
    """A live board hid a 2500 point coin in an alcove behind the red door."""
    grid = [
        ["wall",  "c7",     "wall"],
        ["wall",  "c30",    "wall"],
        ["start", "normal", "normal"],
        ["c40",   "normal", "treasure"],
    ]
    plan = pathfinder.plan({"game_map": grid, "strategy": "max_points", "verbose": True})
    cells = _cells(grid, plan["coords"])
    assert cells.index("c40") < cells.index("c30") < cells.index("c7")
    assert cells[-1] == "treasure"
    assert any("door c30 package included" in note for note in plan["notes"])


def test_extract_key_character_variants():
    assert map_analyzer.extract_key_character("Your key character is: X") == "X"
    assert map_analyzer.extract_key_character("the first character is 'Q'") == "Q"
    assert map_analyzer.extract_key_character("Here is your letter Z for the door") == "Z"


# ---------------------------------------------------------------------------
# registry_lookup (c4)
# ---------------------------------------------------------------------------


def test_slug_candidates_include_acronym_and_hyphenated():
    candidates = registry_lookup.slug_candidates("The Cancer Genome Atlas")
    assert "cancer-genome-atlas" in candidates
    assert "tcga" in candidates


def test_registry_rejects_non_allowlisted_host():
    with pytest.raises(ValueError):
        registry_lookup._assert_public_host("https://example.com/x")


def test_registry_rejects_http_and_metadata():
    with pytest.raises(ValueError):
        registry_lookup._assert_public_host("http://registry.opendata.aws/tcga/")
    with pytest.raises(ValueError):
        registry_lookup._assert_public_host("https://169.254.169.254/latest/meta-data/")


def test_registry_yaml_parsing():
    text = (
        "Name: The Cancer Genome Atlas\n"
        "Description: TCGA molecularly characterized over 20,000 primary cancers.\n"
        "ManagedBy: National Cancer Institute\n"
        "UpdateFrequency: Not updated\n"
        "License: Open\n"
        "Tags:\n  - genomic\n  - life sciences\n"
    )
    fields = registry_lookup.parse_yaml_fields(text)
    assert fields["ManagedBy"] == "National Cancer Institute"
    assert fields["Tags"] == ["genomic", "life sciences"]


# ---------------------------------------------------------------------------
# code_executor sandbox
# ---------------------------------------------------------------------------


def test_executor_runs_arithmetic():
    assert code_executor.run("print(2 + 3 * 4)") == "14"


def test_executor_blocks_imports():
    with pytest.raises(code_executor.UnsafeCodeError):
        code_executor.validate("import os\nprint(os.environ)")


def test_executor_blocks_attribute_access():
    with pytest.raises(code_executor.UnsafeCodeError):
        code_executor.validate("print((1).__class__)")


def test_executor_blocks_dunder_names():
    with pytest.raises(code_executor.UnsafeCodeError):
        code_executor.validate("print(__import__)")


def test_executor_allows_loops_and_functions():
    source = "def f(n):\n    return sum(range(n))\nprint(f(5))"
    assert code_executor.run(source) == "10"


# ---------------------------------------------------------------------------
# pathfinder
# ---------------------------------------------------------------------------

# A: coins, a spike, a challenge, a key and a door. Treasure bottom-right.
GRID = [
    ["start",  "normal", "c7",     "normal", "normal"],
    ["normal", "wall",   "wall",   "wall",   "c8"],
    ["c7",     "normal", "c5",     "normal", "normal"],
    ["normal", "wall",   "wall",   "wall",   "c40"],
    ["normal", "normal", "normal", "c30",    "treasure"],
]


def _plan(**overrides):
    # verbose exposes diagnostics for assertions. Real runs leave it off so the
    # supervisor has almost nothing to echo.
    body = {"game_map": GRID, "strategy": "max_points", "verbose": True}
    body.update(overrides)
    return pathfinder.plan(body)


def _cells(grid, path):
    return [grid[r][c] for r, c in path]


def test_default_output_is_direction_words():
    result = _plan()
    assert all(step in ("up", "down", "left", "right") for step in result["path"])
    assert result["start_position"] == [0, 0]


def test_coords_output_format_is_available():
    result = _plan(output_format="coords")
    assert result["path"] == result["coords"]
    assert result["path"][0] == [0, 0]


def test_directions_and_coords_describe_the_same_route():
    result = _plan()
    rebuilt = [list(result["start_position"])]
    delta = {"up": (-1, 0), "down": (1, 0), "left": (0, -1), "right": (0, 1)}
    for move in result["path"]:
        dr, dc = delta[move]
        rebuilt.append([rebuilt[-1][0] + dr, rebuilt[-1][1] + dc])
    assert rebuilt == result["coords"]


def test_strategy_normalisation_from_navigation_prompt():
    assert pathfinder.normalize_strategy("use strategy max_points") == "max_points"
    assert pathfinder.normalize_strategy("Use strategy max_loot") == "max_loot"
    assert pathfinder.normalize_strategy("use strategy no_door") == "no_door"
    assert pathfinder.normalize_strategy("use strategy get_coins") == "get_coins"
    assert pathfinder.normalize_strategy("use strategy swift") == "swift"
    assert pathfinder.normalize_strategy("") == "max_points"


def test_path_never_steps_on_a_wall_or_off_grid():
    result = _plan()
    rows, cols = len(GRID), len(GRID[0])
    for r, c in result["coords"]:
        assert 0 <= r < rows and 0 <= c < cols
        assert GRID[r][c] != "wall"


def test_path_steps_are_adjacent():
    coords = _plan()["coords"]
    for (r1, c1), (r2, c2) in zip(coords, coords[1:]):
        assert abs(r1 - r2) + abs(c1 - c2) == 1


def test_treasure_is_the_final_tile_only():
    cells = _cells(GRID, _plan()["coords"])
    assert cells[-1] == "treasure"
    assert cells.count("treasure") == 1


def test_max_points_collects_coins():
    result = _plan()
    assert "c7" in _cells(GRID, result["coords"])
    assert result["reaches_treasure"] is True


def test_red_door_package_is_enabled_by_default_leaderboard_policy():
    result = _plan()
    assert any("door c30 package included" in note for note in result["notes"])
    cells = _cells(GRID, result["coords"])
    assert cells.index("c40") < cells.index("c30") < cells.index("treasure")


def test_no_door_strategy_reports_exclusion():
    result = _plan(strategy="use strategy no_door")
    assert any("no_door" in note for note in result["notes"])


def test_safe_strategy_attempts_no_optional_challenges():
    # "safe" selects no targets. Tiles still graded are only those sitting in
    # the sole corridor to the treasure, so it can never exceed max_points.
    safe = _plan(strategy="safe")
    assert safe["reaches_treasure"] is True
    assert safe["challenges_attempted"] <= _plan()["challenges_attempted"]


def test_safe_strategy_skips_optional_challenges_when_a_detour_exists():
    grid = [
        ["start",  "normal", "normal"],
        ["c5",     "wall",   "normal"],
        ["normal", "normal", "treasure"],
    ]
    result = pathfinder.plan({"game_map": grid, "strategy": "safe", "verbose": True})
    assert result["challenges_attempted"] == 0
    assert result["reaches_treasure"] is True


def test_swift_is_not_longer_than_max_points():
    assert _plan(strategy="swift")["steps"] <= _plan()["steps"]


def test_result_is_flat_so_the_engine_regex_matches():
    import re

    body = json.dumps(pathfinder.plan({"game_map": GRID}))
    assert body.startswith('{"answer":')
    # Mirrors path_parser._try_json_path_object, which forbids nested braces.
    # This still has to hold: if the model echoes the whole object instead of the
    # answer field, the engine must recover the route from it rather than fail.
    assert re.search(r'\{[^{}]*"path"\s*:\s*\[.*?\][^{}]*\}', body, re.DOTALL)
    start = tuple(pathfinder.plan({"game_map": GRID})["start_position"])
    assert _engine_parser().parse_navigation_path(body, start) is not None


def _engine_parser():
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "lambda", "agentic-api",
    ))
    import path_parser  # noqa: PLC0415

    return path_parser


def test_every_scored_tool_declares_its_parameters():
    """A missing schema block makes the model guess the argument shape.

    Three live runs in a row wasted a duplicate `courseoptimizer` call doing
    exactly that ("Let me correct the format:"), while `claims`, which has always
    declared `rows`, was called once and cleanly.
    """
    import inspect

    os.environ.setdefault("AGENT_CONFIGURATIONS_TABLE", "SchemaOnly")
    os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "lambda", "agentic-api",
    ))
    import agent_config_handlers  # noqa: PLC0415

    expected = {
        pathfinder: ("route", ["game_map", "start_label", "strategy"]),
        map_analyzer: ("doorkey", ["key_char", "count_word"]),
        claims_eob_solver: ("c3", ["rows"]),
        course_optimizer: ("course_optimizer", ["sections"]),
        permit_calculator: ("c18", ["capacity_mw", "kind"]),
        registry_lookup: ("c4", ["name"]),
    }
    for module, (name, required) in expected.items():
        schemas = agent_config_handlers._try_parse_docstring_schema(
            inspect.getsource(module)
        )
        assert len(schemas) == 1, f"{module.__name__} produced {len(schemas)} schemas"
        assert schemas[0]["name"] == name, module.__name__
        assert schemas[0]["inputSchema"]["required"] == required, module.__name__


def test_c2_pipe_rows_solve_both_live_challenges_in_one_call():
    """The five-field pipe row drops the unused instructor and needs no retry."""
    rows = (
        "SEC-101|BIO-201|MWF 9:00AM|12|35;SEC-102|BIO-201|MWF 11:00AM|14|35;"
        "SEC-103|CHEM-301|TTh 10:00AM|28|30;SEC-104|PHYS-101|MWF 2:00PM|30|35"
    )
    answer = json.loads(
        course_optimizer.lambda_handler({"sections": rows}, None)["body"]
    )["answer"]
    assert answer == (
        '{"FlaggedSections":["SEC-101","SEC-102"],"Consolidations":'
        '[{"keep":"SEC-102","cancel":"SEC-101","combinedEnrollment":26,'
        '"capacity":35}],"NoAction":[]}'
    )

    rows = (
        "SEC-1601|MHA-501|MW 6:00PM|7|25;SEC-1602|MHA-501|TTh 6:00PM|9|25;"
        "SEC-1603|MHA-601|Sat 9:00AM|5|20;SEC-1604|MHA-601|Sat 9:00AM|4|20;"
        "SEC-1605|MHA-701|MW 7:30PM|18|20;SEC-1606|MHA-710|TTh 7:30PM|16|20"
    )
    answer = json.loads(
        course_optimizer.lambda_handler({"sections": rows}, None)["body"]
    )["answer"]
    assert answer == (
        '{"FlaggedSections":["SEC-1601","SEC-1602","SEC-1603","SEC-1604"],'
        '"Consolidations":[{"keep":"SEC-1602","cancel":"SEC-1601",'
        '"combinedEnrollment":16,"capacity":25}],'
        '"NoAction":["SEC-1603","SEC-1604"]}'
    )


def test_c2_still_accepts_the_previously_deployed_shapes():
    """Never break a live payload while changing a tool mid-competition."""
    arrays = [
        ["SEC-101", "BIO-201", "Dr. Smith", "MWF 9:00AM", 12, 35],
        ["SEC-102", "BIO-201", "Dr. Smith", "MWF 11:00AM", 14, 35],
    ]
    objects = [
        {"sectionId": "SEC-101", "courseName": "BIO-201",
         "timeSlot": "MWF 9:00AM", "enrolled": 12, "capacity": 35},
        {"sectionId": "SEC-102", "courseName": "BIO-201",
         "timeSlot": "MWF 11:00AM", "enrolled": 14, "capacity": 35},
    ]
    expected = json.loads(
        course_optimizer.lambda_handler({"sections": arrays}, None)["body"]
    )["answer"]
    for payload in (json.dumps(arrays), objects):
        answer = json.loads(
            course_optimizer.lambda_handler({"sections": payload}, None)["body"]
        )["answer"]
        assert answer == expected


def test_engine_parses_direction_output_back_to_the_same_route():
    parser = _engine_parser()
    plan = pathfinder.plan({"game_map": GRID, "verbose": True})
    parsed = parser.parse_navigation_path(json.dumps(plan), tuple(plan["start_position"]))
    assert parsed == [tuple(step) for step in plan["coords"]]


def test_engine_parses_bare_direction_array():
    parser = _engine_parser()
    plan = pathfinder.plan({"game_map": GRID, "verbose": True})
    # The stock sub-agent prompt returns only the array, so that path matters too.
    parsed = parser.parse_navigation_path(
        json.dumps(plan["path"]), tuple(plan["start_position"])
    )
    assert parsed == [tuple(step) for step in plan["coords"]]


def test_engine_parses_coords_output_format():
    parser = _engine_parser()
    plan = pathfinder.plan({"game_map": GRID, "output_format": "coords", "verbose": True})
    parsed = parser.parse_navigation_path(json.dumps(plan), (0, 0))
    assert parsed == [tuple(step) for step in plan["coords"]]


def test_start_label_removes_the_conversion_that_broke_the_10_42_run():
    """The prompt states the start as a label; converting it is the model's job to lose.

    The 10-42 live run turned E6 into [4,4] instead of [5,4] — right column, wrong
    row — and got a legal-looking 96-move route whose first step was the wall at
    (5,3). A verbatim label cannot be off by one, so it wins over any numeric pair.
    """
    grid = [
        ["c3",     "normal", "c3",     "wall",  "wall",   "wall",   "c18",  "normal", "c18"],
        ["normal", "normal", "normal", "wall",  "c7",     "wall",   "normal", "normal", "normal"],
        ["wall",   "c1",     "wall",   "wall",  "c30",    "wall",   "wall", "c1",     "wall"],
        ["c5",     "normal", "wall",   "wall",  "normal", "normal", "normal", "normal", "c5"],
        ["c7",     "normal", "normal", "c8",    "normal", "normal", "normal", "normal", "treasure"],
        ["c5",     "normal", "wall",   "wall",  "normal", "normal", "c8",   "normal", "c5"],
        ["wall",   "c1",     "wall",   "wall",  "c8",     "wall",   "wall", "c1",     "wall"],
        ["normal", "normal", "normal", "wall",  "normal", "wall",   "normal", "normal", "normal"],
        ["c4",     "normal", "c4",     "wall",  "c40",    "wall",   "c2",   "normal", "c2"],
    ]
    by_label = pathfinder.plan(
        {"game_map": grid, "start_label": "E6", "strategy": "max_points"}
    )
    assert by_label["start_position"] == [5, 4]
    assert len(by_label["path"]) == 97
    assert by_label["path"][0] == "up"

    # A label beats a wrong numeric pair rather than silently routing from it.
    rescued = pathfinder.plan({
        "game_map": grid, "start_label": "E6", "start_pos": [4, 4],
        "strategy": "max_points",
    })
    assert rescued["start_position"] == [5, 4]
    assert rescued["path"] == by_label["path"]

    # The exact failure mode, for the record: that numeric pair alone drops a move.
    wrong = pathfinder.plan({"game_map": grid, "start_pos": [4, 4], "strategy": "max_points"})
    assert len(wrong["path"]) == 96
    assert wrong["path"][0] == "left"


def test_default_response_is_minimal_and_carries_a_ready_answer():
    """Diagnostics stay out; the emit-ready `answer` stays in.

    A verbose response measured 1406 tokens on a real board and zeroed the 1000
    point token bonus, so the payload keeps only four keys. `answer` is the exact
    string the Supervisor emits, which is cheaper than a quoted array and needs
    no conversion on the one turn where a malformed relay ends the run.
    """
    plan = pathfinder.plan({"game_map": GRID})
    assert set(plan) == {"answer", "path", "start_position", "c5"}
    assert "notes" not in plan
    assert "coords" not in plan
    assert "tile_counts" not in plan

    # The emitted string MUST be a minified JSON array plus the census word.
    # A bare comma-separated route produced zero movement on the live engine:
    # the 10-42 run never left the start tile and scored 1,750.
    assert plan["answer"] == (
        json.dumps(plan["path"], separators=(",", ":")) + " " + plan["c5"]
    )
    assert plan["answer"].startswith('["')
    assert plan["answer"].endswith(" " + plan["c5"])
    assert '","' in plan["answer"], "must stay a quoted JSON array, never bare CSV"

    # The emitted string must drive the engine to the same coordinates.
    parser = _engine_parser()
    start = tuple(plan["start_position"])
    assert parser.parse_navigation_path(plan["answer"], start) == parser.parse_navigation_path(
        json.dumps(plan["path"], separators=(",", ":")), start
    )
    # And the array itself must survive the strict JSON-array stage, not just the
    # loose text fallback, because the live engine only has the strict one.
    assert parser._try_json_array(plan["answer"], start) is not None


def test_coords_output_format_answer_stays_engine_parsable():
    plan = pathfinder.plan({"game_map": GRID, "output_format": "coords"})
    parser = _engine_parser()
    assert parser._try_json_array(plan["answer"], (0, 0)) is not None
    assert parser.parse_navigation_path(plan["answer"], (0, 0)) == [
        tuple(step) for step in plan["path"]
    ]


def test_verbose_adds_diagnostics_without_changing_the_route():
    quiet = pathfinder.plan({"game_map": GRID})
    loud = pathfinder.plan({"game_map": GRID, "verbose": True})
    assert quiet["path"] == loud["path"]
    assert "notes" in loud


def test_consumed_spike_is_free_on_revisit():
    grid = [["start", "c8", "c7"], ["wall", "wall", "normal"], ["treasure", "normal", "normal"]]
    # Walk over the spike, to the coin, back over the spike, then to treasure.
    path = [(0, 0), (0, 1), (0, 2), (0, 1), (0, 2), (1, 2), (2, 2), (2, 1), (2, 0)]
    result = pathfinder.simulate_path(
        grid, path, lives=5, tile_cfg=pathfinder.DEFAULT_TILE_CONFIG,
        life_value=250, treasure_bonus=3000, confidence=pathfinder.DEFAULT_CONFIDENCE,
    )
    assert result["lives_remaining"] == 4  # charged once, not twice
    assert result["reached_treasure"] is True


def test_locked_door_is_fatal_in_simulation():
    grid = [["start", "c30", "treasure"]]
    result = pathfinder.simulate_path(
        grid, [(0, 0), (0, 1), (0, 2)], lives=5,
        tile_cfg=pathfinder.DEFAULT_TILE_CONFIG, life_value=250,
        treasure_bonus=3000, confidence=pathfinder.DEFAULT_CONFIDENCE,
    )
    assert result["died"] is True
    assert result["reached_treasure"] is False


def test_key_before_door_unlocks_it():
    grid = [["start", "c40", "c30", "treasure"]]
    result = pathfinder.simulate_path(
        grid, [(0, 0), (0, 1), (0, 2), (0, 3)], lives=5,
        tile_cfg=pathfinder.DEFAULT_TILE_CONFIG, life_value=250, treasure_bonus=3000,
        confidence={**pathfinder.DEFAULT_CONFIDENCE, "c30": 1.0, "c40": 1.0},
    )
    assert result["died"] is False
    assert result["reached_treasure"] is True
    assert result["points"] == 1050  # 50 key + 1000 door


def test_wall_step_is_reported_as_illegal():
    grid = [["start", "wall", "treasure"]]
    result = pathfinder.simulate_path(
        grid, [(0, 0), (0, 1), (0, 2)], lives=5,
        tile_cfg=pathfinder.DEFAULT_TILE_CONFIG, life_value=250,
        treasure_bonus=3000, confidence=pathfinder.DEFAULT_CONFIDENCE,
    )
    assert result["illegal_step"] == [0, 1]
    assert result["lives_remaining"] == 0


def test_earned_points_survive_death():
    grid = [["start", "c7", "c30", "treasure"]]
    result = pathfinder.simulate_path(
        grid, [(0, 0), (0, 1), (0, 2), (0, 3)], lives=5,
        tile_cfg=pathfinder.DEFAULT_TILE_CONFIG, life_value=250,
        treasure_bonus=3000, confidence=pathfinder.DEFAULT_CONFIDENCE,
    )
    assert result["died"] is True
    assert result["points"] == 2500  # coin banked before the fatal door
    assert result["estimated_score_excluding_token_bonus"] == 2500


def test_challenge_budget_limits_attempts():
    grid = [
        ["start", "c5", "c5", "c5"],
        ["c5",    "c5", "c5", "treasure"],
    ]
    result = pathfinder.plan({"game_map": grid, "max_challenges": 2, "verbose": True})
    assert result["challenges_attempted"] <= 3  # budget plus any unavoidable transit
    assert any("budget" in note for note in result["notes"])


def test_tile_overrides_change_selection():
    grid = [["start", "c5", "treasure"]]
    default = pathfinder.plan({"game_map": grid, "verbose": True})
    assert default["expected_points"] > 0

    overridden = pathfinder.plan({
        "game_map": grid,
        "verbose": True,
        "tile_overrides": {"c5": {"points": 0, "damage": 3}},
    })
    assert any("c5 skipped" in note for note in overridden["notes"])


def test_door_included_when_confidence_is_high():
    result = _plan(challenge_confidence={"c30": 0.99, "c40": 0.99})
    assert any("door c30 package included" in note for note in result["notes"])
    cells = _cells(GRID, result["coords"])
    assert cells.index("c40") < cells.index("c30") < cells.index("treasure")


def test_count_tile_field_is_emitted_for_the_door():
    result = _plan()
    assert result["c5"] == pathfinder.number_to_words(1)


def test_lambda_handler_returns_error_without_treasure():
    response = pathfinder.lambda_handler({"game_map": [["start", "normal"]]}, None)
    assert response["statusCode"] == 400
    assert "treasure" in json.loads(response["body"])["error"]


# ---------------------------------------------------------------------------
# Regressions from real boards (game-events-2026-08-25T07-37-10 / -45)
# ---------------------------------------------------------------------------

# The killer layout: the only corridor to the coin at [1,4] runs through the
# locked red door at [2,4]. The planner used to price the door at 5000 instead
# of blocking it, walk through, take 5 damage and finish with score 0.
DOOR_TRAP_GRID = [
    ["wall",   "wall",   "wall",   "wall",   "wall",   "wall"],
    ["wall",   "wall",   "wall",   "wall",   "c7",     "wall"],
    ["wall",   "wall",   "wall",   "wall",   "c30",    "wall"],
    ["wall",   "wall",   "wall",   "wall",   "normal", "wall"],
    ["normal", "normal", "normal", "normal", "normal", "treasure"],
    ["wall",   "wall",   "wall",   "wall",   "start",  "wall"],
]


def test_never_routes_through_a_locked_door_for_loot():
    plan = pathfinder.plan({"game_map": DOOR_TRAP_GRID, "verbose": True})
    cells = _cells(DOOR_TRAP_GRID, plan["coords"])
    assert "c30" not in cells, "walked into a locked door; that is instant death"
    assert plan["reaches_treasure"] is True
    assert plan["expected_lives_remaining"] >= 4


def test_never_returns_a_route_that_dies_or_misses_treasure():
    """The engine executes whatever it is given, so a losing plan scores zero."""
    for grid in (DOOR_TRAP_GRID, GRID):
        plan = pathfinder.plan({"game_map": grid, "verbose": True})
        assert plan["reaches_treasure"] is True
        sim = pathfinder.simulate_path(
            grid, [tuple(p) for p in plan["coords"]], lives=5,
            tile_cfg=pathfinder.DEFAULT_TILE_CONFIG, life_value=250,
            treasure_bonus=3000, confidence=pathfinder.DEFAULT_CONFIDENCE,
        )
        assert sim["died"] is False
        assert sim["illegal_step"] is None
        assert sim["reached_treasure"] is True


def test_free_target_behind_a_lethal_route_is_dropped():
    """A zero-damage coin must be droppable when its only access is fatal.

    Protecting coins from the risk budget previously deadlocked the repair
    loop, which then returned the fatal path unchanged.
    """
    plan = pathfinder.plan({"game_map": DOOR_TRAP_GRID, "verbose": True})
    assert "c7" not in _cells(DOOR_TRAP_GRID, plan["coords"])
    assert plan["reaches_treasure"] is True


def test_locked_door_allowed_only_when_it_is_the_sole_route():
    grid = [["start", "c30", "treasure"]]
    plan = pathfinder.plan({"game_map": grid, "verbose": True})
    # No survivable route exists, so the tool must say so rather than pretend.
    assert any("WARNING" in note or "locked doors" in note for note in plan["notes"])


def test_position_label_parsing():
    # The navigation prompt states the start as a label such as E6.
    assert pathfinder.parse_position("E6") == (5, 4)
    assert pathfinder.parse_position("A1") == (0, 0)
    assert pathfinder.parse_position([5, 4]) == (5, 4)
    assert pathfinder.parse_position({"row": 5, "col": 4}) == (5, 4)
    assert pathfinder.parse_position("[5,4]") == (5, 4)
    assert pathfinder.parse_position(["E", "6"]) == (5, 4)
    assert pathfinder.parse_position(None) is None


def test_grid_start_tile_beats_a_wrong_start_argument():
    """A real run had the sub-agent pass [4,4] when the start was [5,4].

    Every direction then shifted one row and the path walked into a door, so
    the grid's own start tile must win over whatever the model supplies.
    """
    grid = [
        ["normal", "normal", "treasure"],
        ["normal", "start",  "normal"],
    ]
    plan = pathfinder.plan({"game_map": grid, "start_pos": [0, 0], "verbose": True})
    assert plan["start_position"] == [1, 1]
    assert plan["coords"][0] == [1, 1]


def test_start_label_used_when_grid_has_no_start_tile():
    grid = [
        ["normal", "normal", "treasure"],
        ["normal", "normal", "normal"],
    ]
    plan = pathfinder.plan({"game_map": grid, "start_pos": "C2", "verbose": True})
    assert plan["start_position"] == [1, 2]


# The key is accessible only through one spike. Treasure has a safe detour, so
# taking the top corridor is rational only as a complete c40+c30 package.
SPIKE_KEY_DOOR_GRID = [
    ["start",  "c8",     "c40",    "c30",    "treasure"],
    ["normal", "wall",   "wall",   "wall",   "normal"],
    ["normal", "normal", "normal", "normal", "normal"],
]


def _spike_package_plan(door_confidence, **overrides):
    body = {
        "game_map": SPIKE_KEY_DOOR_GRID,
        "strategy": "max_points",
        "challenge_confidence": {"c40": 1.0, "c30": door_confidence},
        "verbose": True,
    }
    body.update(overrides)
    return pathfinder.plan(body)


def test_profitable_spike_key_door_package_is_selected():
    plan = _spike_package_plan(0.85)
    cells = _cells(SPIKE_KEY_DOOR_GRID, plan["coords"])
    assert "c8" in cells
    assert cells.index("c8") < cells.index("c40") < cells.index("c30")
    assert cells[-1] == "treasure"
    assert any("1 incremental spike" in note for note in plan["notes"])


def test_spike_key_is_skipped_when_door_is_disabled():
    plan = _spike_package_plan(1.0, strategy="no_door")
    cells = _cells(SPIKE_KEY_DOOR_GRID, plan["coords"])
    assert "c40" not in cells
    assert "c30" not in cells
    assert "c8" not in cells
    assert cells[-1] == "treasure"


def test_spike_package_below_economic_threshold_is_skipped():
    # One spike costs 250. With a reliable +50 key, door break-even is exactly
    # (4250 - 50) / (1000 + 1000 life bonus + 3000 treasure) = 0.84.
    plan = _spike_package_plan(0.83)
    cells = _cells(SPIKE_KEY_DOOR_GRID, plan["coords"])
    assert "c30" not in cells
    assert "c40" not in cells
    assert any("non-positive expected score" in note for note in plan["notes"])


def test_spike_package_success_survives_and_reaches_treasure():
    plan = _spike_package_plan(0.85)
    result = pathfinder.simulate_path(
        SPIKE_KEY_DOOR_GRID,
        [tuple(pos) for pos in plan["coords"]],
        lives=5,
        tile_cfg=pathfinder.DEFAULT_TILE_CONFIG,
        life_value=250,
        treasure_bonus=3000,
        confidence={**pathfinder.DEFAULT_CONFIDENCE, "c40": 1.0, "c30": 0.85},
        mode="best",
    )
    assert result["died"] is False
    assert result["lives_remaining"] == 4
    assert result["reached_treasure"] is True
    assert result["points"] == 1050


def test_door_expected_score_uses_terminal_outcome_branches():
    grid = [["start", "c40", "c30", "treasure"]]
    result = pathfinder.simulate_path(
        grid,
        [(0, 0), (0, 1), (0, 2), (0, 3)],
        lives=5,
        tile_cfg=pathfinder.DEFAULT_TILE_CONFIG,
        life_value=250,
        treasure_bonus=3000,
        confidence={**pathfinder.DEFAULT_CONFIDENCE, "c40": 1.0, "c30": 0.5},
    )
    # 50 is banked in both outcomes. Success adds 1000+1250+3000; failure dies.
    assert result["estimated_score_excluding_token_bonus"] == 2675
    assert result["treasure_probability"] == 0.5
    assert result["death_probability"] == 0.5


def test_key_door_package_respects_challenge_budget():
    plan = _spike_package_plan(1.0, max_challenges=1)
    cells = _cells(SPIKE_KEY_DOOR_GRID, plan["coords"])
    assert "c30" not in cells
    assert "c40" not in cells


def test_lambda_handler_succeeds_with_minimal_default_response():
    response = pathfinder.lambda_handler({"game_map": GRID}, None)
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert set(body) == {"answer", "path", "start_position", "c5"}


def test_c1_is_targeted_by_default_after_live_guardrail_verification():
    grid = [
        ["start", "c1",     "treasure"],
        ["normal", "normal", "normal"],
    ]
    plan = pathfinder.plan({"game_map": grid, "strategy": "max_points", "verbose": True})
    assert "c1" in _cells(grid, plan["coords"])
    assert plan["visited_tiles"] == ["c1"]
    assert pathfinder.DEFAULT_CONFIDENCE["c1"] == 0.97


def test_c1_can_be_disabled_when_guardrail_is_unverified():
    grid = [
        ["start", "c1",     "treasure"],
        ["normal", "normal", "normal"],
    ]
    plan = pathfinder.plan({
        "game_map": grid,
        "strategy": "max_points",
        "challenge_confidence": {"c1": 0.0},
        "verbose": True,
    })
    assert "c1" not in _cells(grid, plan["coords"])
    assert any("c1 skipped" in note for note in plan["notes"])


RISK_REPAIR_PACKAGE_GRID = [
    ["start",  "c8",   "c40",  "c30",  "treasure"],
    ["normal", "wall", "wall", "wall", "normal"],
    ["c17",    "normal", "normal", "normal", "normal"],
]


def test_profitable_red_package_survives_aggregate_risk_repair():
    """Repair maximises expected score, so positive-value targets are all kept.

    The planning damage here is 4.20 of 5 lives, which the old damage-budget
    heuristic would have "repaired" by deleting targets. Both the package and
    the positive-value c17 are worth more than the reserve they consume.
    """
    plan = pathfinder.plan({
        "game_map": RISK_REPAIR_PACKAGE_GRID,
        "strategy": "max_points",
        "challenge_confidence": {"c17": 0.61, "c40": 1.0, "c30": 0.90},
        "verbose": True,
    })
    cells = _cells(RISK_REPAIR_PACKAGE_GRID, plan["coords"])
    assert cells.index("c8") < cells.index("c40") < cells.index("c30")
    assert cells[-1] == "treasure"
    assert "c17" in plan["visited_tiles"]
    assert any("door c30 package included" in note for note in plan["notes"])
    assert not any("dropped c30" in note for note in plan["notes"])
    assert not any("removed" in note for note in plan["notes"])


def test_package_removal_uses_exact_coordinates_not_tile_ids():
    key_a, key_b, door = (0, 1), (2, 1), (0, 2)
    remaining = pathfinder._remove_target_unit(
        [key_a, key_b, door], (key_a, door), [(key_a, door)]
    )
    assert key_a not in remaining
    assert door not in remaining
    assert key_b in remaining


ORPHAN_GRID = [
    ["wall",   "wall",   "c4",     "wall", "normal"],
    ["start",  "c8",     "normal", "wall", "normal"],
    ["normal", "wall",   "c40",    "c30",  "treasure"],
    ["normal", "wall",   "wall",   "wall", "normal"],
    ["normal", "normal", "normal", "normal", "normal"],
]


def test_key_and_door_are_never_orphaned():
    """A key is only ever routed together with its matching door."""
    for door_confidence in (0.0, 0.50, 0.82, 0.95, 1.0):
        plan = pathfinder.plan({
            "game_map": ORPHAN_GRID,
            "strategy": "max_points",
            "challenge_confidence": {"c4": 0.60, "c40": 1.0, "c30": door_confidence},
            "verbose": True,
        })
        cells = _cells(ORPHAN_GRID, plan["coords"])
        assert ("c40" in cells) == ("c30" in cells), door_confidence
        if "c30" in cells:
            assert cells.index("c40") < cells.index("c30")
        assert cells[-1] == "treasure"


def test_unprofitable_door_leaves_both_key_and_door_untouched():
    plan = pathfinder.plan({
        "game_map": ORPHAN_GRID,
        "strategy": "max_points",
        "challenge_confidence": {"c4": 0.60, "c40": 1.0, "c30": 0.50},
        "verbose": True,
    })
    cells = _cells(ORPHAN_GRID, plan["coords"])
    assert "c40" not in cells
    assert "c30" not in cells
    assert cells[-1] == "treasure"
    assert not any("package included" in note for note in plan["notes"])


def test_zero_damage_coins_are_never_sacrificed_to_a_damage_budget():
    """Regression for the live 9x9 board that lost 5000 points of coins.

    Two forced spikes plus the red door put planning damage at 4.25 of 5 lives.
    The old repair loop chased that budget and, with challenges protected, the
    only droppable targets left were the two 2500 point coins.
    """
    grid = [
        ["c3",     "normal", "c3",     "wall",  "wall",   "wall",   "c18",  "normal", "c18"],
        ["normal", "normal", "normal", "wall",  "c7",     "wall",   "wall", "normal", "normal"],
        ["wall",   "c1",     "normal", "wall",  "c30",    "wall",   "wall", "c1",     "wall"],
        ["c5",     "normal", "wall",   "wall",  "normal", "normal", "normal", "normal", "c5"],
        ["c7",     "normal", "normal", "c8",    "normal", "wall",   "wall", "normal", "treasure"],
        ["c5",     "normal", "wall",   "wall",  "start",  "wall",   "c8",   "normal", "c5"],
        ["wall",   "c1",     "wall",   "wall",  "c8",     "wall",   "wall", "c1",     "wall"],
        ["normal", "normal", "normal", "wall",  "normal", "wall",   "wall", "normal", "normal"],
        ["c4",     "normal", "c4",     "wall",  "c40",    "wall",   "c2",   "normal", "c2"],
    ]
    plan = pathfinder.plan({"game_map": grid, "strategy": "max_points", "verbose": True})
    visited = plan["visited_tiles"]
    assert visited.count("c7") == 2, "a 2500 point coin was sacrificed"
    for tile, expected in (("c5", 4), ("c1", 4), ("c2", 2), ("c3", 2), ("c4", 2), ("c18", 2)):
        assert visited.count(tile) == expected, tile
    cells = _cells(grid, plan["coords"])
    assert cells.index("c40") < cells.index("c30")
    assert cells[-1] == "treasure"
    assert plan["c5"] == "FOUR"
    assert len(plan["path"]) == 97
    game_facing = json.dumps(plan["path"], separators=(",", ":"))
    assert hashlib.sha256(game_facing.encode()).hexdigest() == (
        "bf4ea148c6acd6b0fdc565d1183c57364f6c94751ddd37c177da578ae33b0f57"
    )
    parsed = _engine_parser().parse_navigation_path(
        game_facing, tuple(plan["start_position"])
    )
    assert parsed == [tuple(step) for step in plan["coords"]]
    assert not any("dropped" in note for note in plan["notes"])


def test_named_recovery_log_route_replays_as_a_separate_bare_full_clear():
    """Protect the accepted 06:26 route without conflating it with local output."""
    bare_path = (
        '["up","left","left","left","left","up","down","down","up",'
        '"right","right","right","right","up","right","right","right",'
        '"right","left","down","down","right","left","down","down",'
        '"down","left","right","right","up","left","up","up","up",'
        '"up","up","up","up","left","right","right","down","left",'
        '"down","down","down","left","left","left","left","left",'
        '"left","up","up","up","up","left","right","right","down",'
        '"left","down","down","down","down","down","down","down",'
        '"left","right","right","up","left","up","up","up","right",'
        '"right","right","down","down","down","down","up","up","up",'
        '"up","up","up","up","down","down","down","right","right",'
        '"right","right"]'
    )
    assert hashlib.sha256(bare_path.encode()).hexdigest() == (
        "e46dc7160cb0b2da790d706e34a3d9675182e1f3465cba19f7cffcdf438e7723"
    )
    assert bare_path.startswith("[") and bare_path.endswith("]")
    assert "`" not in bare_path and "Pathfinding" not in bare_path

    directions = json.loads(bare_path)
    assert len(directions) == 97
    parsed = _engine_parser().parse_navigation_path(bare_path, (5, 4))
    assert parsed is not None and len(parsed) == 98

    required_positions = {
        (0, 0), (0, 2),                   # c3
        (0, 6), (0, 8),                   # c18
        (1, 4), (4, 0),                   # coins
        (2, 1), (2, 7), (6, 1), (6, 7),  # c1
        (2, 4),                            # c30
        (3, 0), (3, 8), (5, 0), (5, 8),  # c5
        (8, 0), (8, 2),                   # c4
        (8, 4),                            # c40
        (8, 6), (8, 8),                   # c2
        (4, 8),                            # treasure
    }
    assert all(0 <= row < 9 and 0 <= col < 9 for row, col in parsed)
    assert required_positions <= set(parsed)
    assert parsed.index((8, 4)) < parsed.index((2, 4))
    assert parsed[-1] == (4, 8)


def test_unselected_key_is_avoided_when_a_detour_exists():
    grid = [
        ["start", "c40", "treasure"],
        ["normal", "normal", "normal"],
    ]
    plan = pathfinder.plan({"game_map": grid, "strategy": "no_door", "verbose": True})
    assert "c40" not in _cells(grid, plan["coords"])
    assert _cells(grid, plan["coords"])[-1] == "treasure"
