"""Machine-readable Workbench runtime contract."""

from __future__ import annotations

import ast
from dataclasses import fields
import importlib
import json
import re
import shlex
from pathlib import Path
from typing import Any

CONTRACT_PATH = Path(__file__).resolve().parents[1] / "config" / "runtime_contract.json"
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "runtime_contract.schema.json"
WORKBENCH_MAIN_PATH = Path(__file__).resolve().parents[1] / "__main__.py"
FLOW_STATE_PATH = Path(__file__).resolve().parents[1] / "ui" / "flow_state.py"
RUNTIME_STATE_REF_PATTERN = r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+$"
RUNTIME_STATE_ROOTS = {
    "RunEvidenceSnapshot",
    "data",
    "diagnostics",
    "latency",
    "personal",
    "registry",
    "system",
    "ui",
}
RUNTIME_STATE_NESTED_REFS = {
    "data.event_universe",
    "data.rithmic_trial",
    "diagnostics.feature_fabric",
    "latency.ibkr_endpoint",
    "latency.rithmic_endpoint",
    "latency.rithmic_order_ack",
    "personal.sandbox_lock",
    "personal.selected_model",
    "personal.selected_symbol",
    "registry.event_universe",
    "registry.lane_registry",
    "system.feature_fabric",
    "system.ibkr_endpoint",
    "system.lane_registry",
    "system.llm_providers",
    "system.rithmic_endpoint",
    "ui.session_state.wb_audit_grade",
    "ui.session_state.wb_defensive_stubs",
    "ui.session_state.wb_wallet_activity",
    "ui.session_state.wb_wallet_last_receive",
    "ui.session_state.wb_wallet_last_send",
    "ui.session_state.wb_wallet_passphrase_nonce",
    "ui.session_state.wb_wallet_refreshed_at",
    "ui.session_state.wb_wallet_refresh_seconds",
    "ui.session_state.wb_wallet_send_message",
    "ui.session_state.wb_wallet_send_preview",
    "ui.session_state.wb_wallet_snapshot",
}
UTILITY_CLI_COMMAND_SCOPES = {
    "ibkr-endpoint": "non_cme_endpoint_diagnostic",
    "list": "registry_read",
    "setup": "environment_setup",
}
SNAPSHOT_RUN_STATES = {
    "idle",
    "blocked",
    "fresh",
    "planned",
    "catalogued",
    "observed_blocked",
}
FLOW_PROGRESS_STATES = {"running", "paused"}
RUN_STATE_ORDER = (
    "idle",
    "running",
    "paused",
    "blocked",
    "fresh",
    "planned",
    "catalogued",
    "observed_blocked",
    "data_insufficient",
    "pass",
    "fail",
    "conditional",
    "cancelled",
    "dry_run",
    "unknown",
    "complete",
    "completed",
)
CAMPAIGN_STATE_ORDER = (
    "DRY_RUN",
    "DATA_INSUFFICIENT",
    "PASS",
    "FAIL",
    "BLOCKED",
    "CANCELLED",
    "CONDITIONAL",
)
ARTIFACT_COVERAGE_STATE_ORDER = (
    "OBSERVED",
    "OBSERVED_DIAGNOSTIC_ONLY",
    "PRESENT_NOT_WIRED",
    "BLOCKING",
    "MISSING",
    "STALE",
    "CONFIGURED_NOT_OBSERVED",
    "NOT_CONFIGURED",
)
ARTIFACT_STAGE_STATE_ORDER = (
    "observed",
    "observed_unlinked",
    "missing",
    "done",
    "blocked",
    "pending",
    "stale_blocked",
    "loaded_by_live_monitor",
    "not_applicable",
    "UNKNOWN",
    "unknown",
    "error",
    "ERROR",
    "artifact_error",
    "ARTIFACT_ERROR",
    "not_observed",
    "NOT_OBSERVED",
    "observed_trade_only_replay",
    "report_binding_blocked",
    "PENDING",
    "INSUFFICIENT_ORDER_ACK_EVIDENCE",
    "BLOCKING",
    "PASS",
    "FAIL",
    "MISSING",
    "STALE",
    "DISABLED",
    "SKIPPED",
    "CONFIGURED_NOT_OBSERVED",
)
ARTIFACT_STATE_ORDER = ARTIFACT_COVERAGE_STATE_ORDER + ARTIFACT_STAGE_STATE_ORDER
DATA_STATE_ORDER = (
    "SOURCED",
    "SEED",
    "DISABLED",
    "RETIRED",
    "BELOW_MINIMUM",
    "MINIMUM_ONLY",
    "TARGET_MET",
    "ABOVE_TARGET",
)
ROBUSTNESS_STATE_ORDER = (
    "PENDING",
    "PASS",
    "CONDITIONAL",
    "FAIL",
    "ERROR",
    "SKIPPED",
    "PENDING_PHASE9_METRICS",
    "FAILED_PHASE9_CHECKS",
    "EMPTY",
    "INCOMPLETE",
    "single_window",
)
SIM_SHADOW_STATE_ORDER = (
    "PASS",
    "FAIL",
    "pending_CHI404",
)
RITHMIC_ORDER_ACK_STATE_ORDER = (
    "MEASURED",
    "REPORT_BINDING_BLOCKED",
    "INSUFFICIENT_ORDER_ACK_EVIDENCE",
    "READY_TO_CONNECT",
    "CONNECTED",
    "BLOCKED",
    "CONFIGURED_NOT_AUTHENTICATED",
    "CONFIGURED_NOT_OBSERVED",
    "not_applicable",
)
DOWNLOAD_RESPONSE_CONTRACT = {
    "required": ["status", "blocking", "error"],
    "status_values": ["PASS", "BLOCKING"],
    "blocking_item_required": ["gate", "status", "reason"],
}


def allowed_runtime_state_refs() -> set[str]:
    snapshot_refs = {f"RunEvidenceSnapshot.{field}" for field in _run_evidence_snapshot_fields()}
    return snapshot_refs | RUNTIME_STATE_NESTED_REFS


def _ordered_states(states: set[str], preferred_order: tuple[str, ...]) -> list[str]:
    ordered = []
    seen = set()
    for state in preferred_order:
        if state in states and state not in seen:
            ordered.append(state)
            seen.add(state)
    return ordered + sorted(states - seen)


def _string_literals(node: ast.AST) -> set[str]:
    return {
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    }


def _literal_assignment_values(
    path: Path,
    target_name: str,
    *,
    exclude_literals: set[str] | None = None,
) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    excluded = exclude_literals or set()
    values: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == target_name for target in node.targets):
            continue
        value = node.value
        if isinstance(value, ast.Call) and value.args:
            value = value.args[0]
        values.update(_string_literals(value) - excluded)
    return values


def expected_run_states() -> list[str]:
    from workbench.src.run.campaign_runner import CAMPAIGN_RUN_STATES

    terminal_states = _literal_assignment_values(FLOW_STATE_PATH, "_TERMINAL_STATES")
    return _ordered_states(
        SNAPSHOT_RUN_STATES | FLOW_PROGRESS_STATES | terminal_states | set(CAMPAIGN_RUN_STATES),
        RUN_STATE_ORDER,
    )


def expected_campaign_states() -> list[str]:
    from workbench.src.run.campaign_runner import CAMPAIGN_RESULT_STATUSES

    return list(CAMPAIGN_RESULT_STATUSES)


def expected_all_lanes_terminal_states() -> list[str]:
    from workbench.src.run.all_lanes import TERMINAL_STATES

    return sorted(str(state) for state in TERMINAL_STATES)


def expected_model_states() -> list[str]:
    return expected_all_lanes_terminal_states()


def expected_artifact_states() -> list[str]:
    from workbench.src.run.evidence_snapshot import ARTIFACT_STATES

    return _ordered_states({str(state) for state in ARTIFACT_STATES}, ARTIFACT_STATE_ORDER)


def expected_artifact_coverage_states() -> list[str]:
    from workbench.src.run.evidence_snapshot import ARTIFACT_COVERAGE_STATUSES

    return _ordered_states({str(state) for state in ARTIFACT_COVERAGE_STATUSES}, ARTIFACT_COVERAGE_STATE_ORDER)


def expected_artifact_stage_states() -> list[str]:
    from workbench.src.run.evidence_snapshot import ARTIFACT_STAGE_STATUSES

    return _ordered_states({str(state) for state in ARTIFACT_STAGE_STATUSES}, ARTIFACT_STAGE_STATE_ORDER)


def expected_event_row_states() -> list[str]:
    from economic_event_universe.service import VALID_ROW_STATUSES

    return _ordered_states({str(state) for state in VALID_ROW_STATUSES}, DATA_STATE_ORDER)


def expected_data_coverage_states() -> list[str]:
    from workbench.src.data.coverage_check import MIN_VALID_TRADING_DAYS, TARGET_VALID_TRADING_DAYS, _coverage_status

    statuses = {
        _coverage_status(0),
        _coverage_status(MIN_VALID_TRADING_DAYS),
        _coverage_status(TARGET_VALID_TRADING_DAYS),
        _coverage_status(TARGET_VALID_TRADING_DAYS + 1),
    }
    return _ordered_states({str(state) for state in statuses}, DATA_STATE_ORDER)


def expected_data_states() -> list[str]:
    return _ordered_states(set(expected_event_row_states()) | set(expected_data_coverage_states()), DATA_STATE_ORDER)


def expected_robustness_states() -> list[str]:
    from workbench.src.robustness.pack import ROBUSTNESS_CHECK_STATUSES, ROBUSTNESS_WALK_FORWARD_STATUSES
    from workbench.src.run.campaign_runner import CAMPAIGN_WFC_STATUSES, ROBUSTNESS_INPUT_STATUSES

    statuses = (
        set(ROBUSTNESS_CHECK_STATUSES)
        | set(ROBUSTNESS_WALK_FORWARD_STATUSES)
        | set(CAMPAIGN_WFC_STATUSES)
        | set(ROBUSTNESS_INPUT_STATUSES)
    )
    return _ordered_states(statuses, ROBUSTNESS_STATE_ORDER)


def expected_sim_shadow_states() -> list[str]:
    from workbench.src.run.campaign_runner import SIM_SHADOW_STATUSES

    return _ordered_states(set(SIM_SHADOW_STATUSES), SIM_SHADOW_STATE_ORDER)


def expected_rithmic_endpoint_states() -> list[str]:
    from data_system.rithmic_trial.endpoint_status import RITHMIC_ENDPOINT_STATUSES

    return list(RITHMIC_ENDPOINT_STATUSES)


def expected_rithmic_order_ack_states() -> list[str]:
    from workbench.src.run.evidence_snapshot import RITHMIC_ORDER_ACK_STATUSES

    ack_states = {str(state) for state in RITHMIC_ORDER_ACK_STATUSES}
    return _ordered_states(ack_states | set(expected_rithmic_endpoint_states()), RITHMIC_ORDER_ACK_STATE_ORDER)


REQUIRED_TAB_FIELDS = {
    "name",
    "purpose",
    "frontend_component",
    "action_components",
    "backend_service",
    "runtime_state",
    "required_config",
    "required_artifacts",
    "required_schema",
    "pipeline_dependency",
    "expected_output",
    "error_behavior",
    "allowed_actions",
}


def _resolve_ref(schema: dict[str, Any], ref: str) -> dict[str, Any]:
    prefix = "#/$defs/"
    if not ref.startswith(prefix):
        raise ValueError(f"unsupported schema ref: {ref}")
    return schema["$defs"][ref.removeprefix(prefix)]


def _json_type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int | float):
        return "number"
    if value is None:
        return "null"
    return type(value).__name__


def _validate_schema_node(value: Any, node: dict[str, Any], root_schema: dict[str, Any], path: str) -> list[str]:
    if "$ref" in node:
        return _validate_schema_node(value, _resolve_ref(root_schema, str(node["$ref"])), root_schema, path)

    errors: list[str] = []
    if "const" in node and value != node["const"]:
        errors.append(f"{path} must be {node['const']!r}")
    enum_values = node.get("enum")
    if isinstance(enum_values, list) and value not in enum_values:
        errors.append(f"{path} must be one of {enum_values!r}")

    expected_type = node.get("type")
    if expected_type:
        type_matches = {
            "object": isinstance(value, dict),
            "array": isinstance(value, list),
            "string": isinstance(value, str),
            "boolean": isinstance(value, bool),
        }.get(str(expected_type), True)
        if not type_matches:
            errors.append(f"{path} must be {expected_type}, got {_json_type_name(value)}")
            return errors

    if isinstance(value, str) and int(node.get("minLength", 0)) > len(value):
        errors.append(f"{path} must not be empty")
    pattern = node.get("pattern")
    if isinstance(value, str) and isinstance(pattern, str) and re.search(pattern, value) is None:
        errors.append(f"{path} must match pattern {pattern!r}")

    if isinstance(value, list):
        min_items = node.get("minItems")
        if isinstance(min_items, int) and len(value) < min_items:
            errors.append(f"{path} must contain at least {min_items} item(s)")
        if node.get("uniqueItems"):
            seen = set()
            for item in value:
                marker = json.dumps(item, sort_keys=True)
                if marker in seen:
                    errors.append(f"{path} items must be unique")
                    break
                seen.add(marker)
        item_schema = node.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(_validate_schema_node(item, item_schema, root_schema, f"{path}[{index}]"))

    if isinstance(value, dict):
        properties = node.get("properties") or {}
        required = node.get("required") or []
        for field in required:
            if field not in value:
                errors.append(f"{path} missing required field: {field}")
        if node.get("additionalProperties") is False:
            extra = sorted(set(value) - set(properties))
            for field in extra:
                errors.append(f"{path} has unexpected field: {field}")
        for field, child_schema in properties.items():
            if field in value and isinstance(child_schema, dict):
                child_path = field if path == "$" else f"{path}.{field}"
                errors.extend(_validate_schema_node(value[field], child_schema, root_schema, child_path))

    return errors


def validate_runtime_contract_schema(contract: dict[str, Any], schema: dict[str, Any] | None = None) -> list[str]:
    root_schema = schema or json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return _validate_schema_node(contract, root_schema, root_schema, "$")


def _validate_state_vocabulary(
    payload: dict[str, Any],
    errors: list[str],
    *,
    field: str,
    expected: list[str],
    source_label: str,
) -> None:
    actual = payload.get(field)
    if not isinstance(actual, list):
        errors.append(f"{field} must be a list")
        return
    if actual != expected:
        errors.append(
            f"{field} must match {source_label}: expected {expected!r}, got {actual!r}"
        )


def _run_evidence_snapshot_fields() -> set[str]:
    from workbench.src.run.evidence_snapshot import RunEvidenceSnapshot

    return {field.name for field in fields(RunEvidenceSnapshot)}


def _runtime_state_ref_error(reference: Any) -> str | None:
    if not isinstance(reference, str) or not reference.strip():
        return "must be a non-empty runtime state ref"
    if not re.fullmatch(RUNTIME_STATE_REF_PATTERN, reference):
        return f"must be a machine runtime state ref matching {RUNTIME_STATE_REF_PATTERN!r}"
    root, _, remainder = reference.partition(".")
    if root not in RUNTIME_STATE_ROOTS:
        return f"references unknown runtime state root: {root!r}"
    if root == "RunEvidenceSnapshot":
        parts = remainder.split(".")
        field = parts[0]
        if field not in _run_evidence_snapshot_fields():
            return f"references unknown RunEvidenceSnapshot field: {field!r}"
        if len(parts) > 1:
            return f"references undocumented runtime state ref: {reference!r}"
    elif reference not in RUNTIME_STATE_NESTED_REFS:
        return f"references undocumented runtime state ref: {reference!r}"
    return None


def _resolve_dotted_reference(reference: Any, *, allow_module: bool, require_callable: bool) -> str | None:
    if not isinstance(reference, str) or not reference.strip():
        return "must be a non-empty dotted import path"
    dotted_path = reference.strip()
    if "." not in dotted_path:
        return f"{dotted_path!r} must include a module and attribute"

    parts = dotted_path.split(".")
    last_import_error: Exception | None = None
    for split_at in range(len(parts), 0, -1):
        module_name = ".".join(parts[:split_at])
        attr_parts = parts[split_at:]
        try:
            resolved = importlib.import_module(module_name)
        except Exception as exc:
            last_import_error = exc
            continue
        if not attr_parts:
            if allow_module:
                return None
            return f"{dotted_path!r} must name a module attribute"
        for attr in attr_parts:
            if not hasattr(resolved, attr):
                return f"{dotted_path!r} missing attribute {attr!r} on {module_name!r}"
            resolved = getattr(resolved, attr)
        if require_callable and not callable(resolved):
            return f"{dotted_path!r} must resolve to a callable"
        return None

    detail = f": {last_import_error}" if last_import_error else ""
    return f"{dotted_path!r} could not import a module{detail}"


def _workbench_cli_subcommands(path: Path | None = None) -> set[str]:
    cli_path = path or WORKBENCH_MAIN_PATH
    tree = ast.parse(cli_path.read_text(encoding="utf-8"), filename=str(cli_path))
    commands: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "add_parser":
            continue
        if not isinstance(node.func.value, ast.Name) or node.func.value.id != "sub":
            continue
        if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            commands.add(node.args[0].value)
    return commands


def _literal_command_compare(node: ast.AST) -> tuple[str, ast.cmpop] | None:
    if not isinstance(node, ast.Compare):
        return None
    left = node.left
    if not (
        isinstance(left, ast.Attribute)
        and left.attr == "command"
        and isinstance(left.value, ast.Name)
        and left.value.id == "args"
    ):
        return None
    if len(node.ops) != 1 or not isinstance(node.ops[0], (ast.Eq, ast.NotEq)):
        return None
    if len(node.comparators) != 1:
        return None
    comparator = node.comparators[0]
    if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
        return comparator.value, node.ops[0]
    return None


def _body_has_return(statements: list[ast.stmt]) -> bool:
    return any(isinstance(node, ast.Return) for statement in statements for node in ast.walk(statement))


def _collect_dispatch_commands(statements: list[ast.stmt], handled: set[str]) -> None:
    for index, statement in enumerate(statements):
        if not isinstance(statement, ast.If):
            continue
        command_compare = _literal_command_compare(statement.test)
        if command_compare is not None:
            command, operator = command_compare
            if isinstance(operator, ast.Eq):
                handled.add(command)
            elif _body_has_return(statement.body) and index + 1 < len(statements):
                handled.add(command)
        _collect_dispatch_commands(statement.body, handled)
        _collect_dispatch_commands(statement.orelse, handled)


def _workbench_cli_dispatch_commands(path: Path | None = None) -> set[str]:
    cli_path = path or WORKBENCH_MAIN_PATH
    tree = ast.parse(cli_path.read_text(encoding="utf-8"), filename=str(cli_path))
    handled: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            _collect_dispatch_commands(node.body, handled)
            break
    return handled


def _workbench_cli_command(cli: Any) -> str | None:
    if not isinstance(cli, str):
        return None
    try:
        parts = shlex.split(cli)
    except ValueError:
        return None
    if len(parts) == 4 and parts[:3] == ["python", "-m", "workbench"]:
        return parts[3]
    return None


def _call_keyword_value(call: ast.Call, name: str) -> Any:
    for keyword in call.keywords:
        if keyword.arg == name and isinstance(keyword.value, ast.Constant):
            return keyword.value.value
    return None


def _add_parser_command(call: ast.Call) -> str | None:
    if not isinstance(call.func, ast.Attribute) or call.func.attr != "add_parser":
        return None
    if not isinstance(call.func.value, ast.Name) or call.func.value.id != "sub":
        return None
    if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
        return call.args[0].value
    return None


def _argument_dest(call: ast.Call) -> str | None:
    explicit_dest = _call_keyword_value(call, "dest")
    if isinstance(explicit_dest, str) and explicit_dest:
        return explicit_dest.replace("-", "_")
    arg_names = [
        arg.value
        for arg in call.args
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
    ]
    if not arg_names:
        return None
    options = [name for name in arg_names if name.startswith("-")]
    if options:
        long_options = [name for name in options if name.startswith("--")]
        selected = long_options[0] if long_options else options[0]
        return selected.lstrip("-").replace("-", "_")
    return arg_names[0].replace("-", "_")


def _argument_bucket(call: ast.Call) -> str:
    action = _call_keyword_value(call, "action")
    if action in {"store_true", "store_false"}:
        return "flags"
    arg_names = [
        arg.value
        for arg in call.args
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
    ]
    positional = bool(arg_names) and not any(name.startswith("-") for name in arg_names)
    if positional or _call_keyword_value(call, "required") is True:
        return "required"
    return "optional"


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _workbench_cli_request_args(path: Path | None = None) -> dict[str, dict[str, list[str]]]:
    cli_path = path or WORKBENCH_MAIN_PATH
    tree = ast.parse(cli_path.read_text(encoding="utf-8"), filename=str(cli_path))
    parser_vars: dict[str, str] = {}
    commands: dict[str, dict[str, list[str]]] = {}

    for node in ast.walk(tree):
        call: ast.Call | None = None
        target_name = ""
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            call = node.value
            if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                target_name = node.targets[0].id
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
        if call is None:
            continue
        command = _add_parser_command(call)
        if command is None:
            continue
        commands.setdefault(command, {"required": [], "optional": [], "flags": []})
        if target_name:
            parser_vars[target_name] = command

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "add_argument":
            continue
        if not isinstance(node.func.value, ast.Name):
            continue
        command = parser_vars.get(node.func.value.id)
        if command is None:
            continue
        dest = _argument_dest(node)
        if dest is None:
            continue
        _append_unique(commands[command][_argument_bucket(node)], dest)
    return commands


def expected_workbench_cli_request_args() -> dict[str, dict[str, list[str]]]:
    return _workbench_cli_request_args()


def expected_download_response_contract() -> dict[str, list[str]]:
    return {key: list(value) for key, value in DOWNLOAD_RESPONSE_CONTRACT.items()}


def _validate_cli_request_args(
    errors: list[str],
    *,
    collection_name: str,
    index: int,
    entry: dict[str, Any],
    command: str,
    expected_by_command: dict[str, dict[str, list[str]]],
) -> None:
    expected = expected_by_command.get(command)
    if expected is None:
        return
    actual = entry.get("request_args")
    if actual != expected:
        errors.append(
            f"{collection_name}[{index}].request_args must match Workbench CLI argparse for {command!r}: "
            f"expected {expected!r}, got {actual!r}"
        )


def _validate_download_response_contract(errors: list[str], index: int, endpoint: dict[str, Any]) -> None:
    actual = endpoint.get("response_contract")
    expected = expected_download_response_contract()
    if actual != expected:
        errors.append(
            f"backend_endpoints[{index}].response_contract must match Workbench download response contract: "
            f"expected {expected!r}, got {actual!r}"
        )


def load_runtime_contract(path: Path | None = None) -> dict[str, Any]:
    contract_path = path or CONTRACT_PATH
    return json.loads(contract_path.read_text(encoding="utf-8"))


def contract_tabs(contract: dict[str, Any] | None = None) -> list[str]:
    payload = contract or load_runtime_contract()
    return [str(tab["name"]) for tab in payload.get("tabs", [])]


def validate_runtime_contract(contract: dict[str, Any] | None = None) -> list[str]:
    payload = contract or load_runtime_contract()
    errors: list[str] = validate_runtime_contract_schema(payload)
    if payload.get("schema_version") != "workbench_runtime_contract_v1":
        errors.append("schema_version must be workbench_runtime_contract_v1")
    tabs = payload.get("tabs")
    if not isinstance(tabs, list) or not tabs:
        errors.append("tabs must be a non-empty list")
        tabs = []
    names: list[str] = []
    for index, tab in enumerate(tabs):
        if not isinstance(tab, dict):
            errors.append(f"tabs[{index}] must be an object")
            continue
        missing = sorted(REQUIRED_TAB_FIELDS - set(tab))
        if missing:
            errors.append(f"{tab.get('name', f'tabs[{index}]')} missing fields: {', '.join(missing)}")
        name = str(tab.get("name") or "")
        if not name:
            errors.append(f"tabs[{index}] missing name")
        names.append(name)
        for field in (
            "runtime_state",
            "required_config",
            "required_artifacts",
            "required_schema",
            "pipeline_dependency",
            "action_components",
            "allowed_actions",
        ):
            if field in tab and not isinstance(tab[field], list):
                errors.append(f"{name}.{field} must be a list")
        for field in ("frontend_component", "backend_service"):
            if field in tab:
                error = _resolve_dotted_reference(
                    tab[field],
                    allow_module=(field == "backend_service"),
                    require_callable=True,
                )
                if error:
                    errors.append(f"{name}.{field} {error}")
        if isinstance(tab.get("action_components"), list):
            for component_index, component in enumerate(tab["action_components"]):
                error = _resolve_dotted_reference(component, allow_module=False, require_callable=True)
                if error:
                    errors.append(f"{name}.action_components[{component_index}] {error}")
        if isinstance(tab.get("runtime_state"), list):
            for state_index, state_ref in enumerate(tab["runtime_state"]):
                error = _runtime_state_ref_error(state_ref)
                if error:
                    errors.append(f"tabs[{index}].runtime_state[{state_index}] {error}")
    if len(names) != len(set(names)):
        errors.append("tab names must be unique")
    run_sources = payload.get("run_sources")
    if not isinstance(run_sources, list) or not run_sources:
        errors.append("run_sources must be a non-empty list")
    else:
        from workbench.src.run.evidence_snapshot import workbench_run_sources

        expected_sources = workbench_run_sources()
        if run_sources != expected_sources:
            errors.append(
                "run_sources must match workbench_run_sources(): "
                f"expected {expected_sources!r}, got {run_sources!r}"
            )
    _validate_state_vocabulary(
        payload,
        errors,
        field="model_states",
        expected=expected_model_states(),
        source_label="all_lanes.TERMINAL_STATES",
    )
    _validate_state_vocabulary(
        payload,
        errors,
        field="run_states",
        expected=expected_run_states(),
        source_label="Workbench snapshot/flow states",
    )
    _validate_state_vocabulary(
        payload,
        errors,
        field="campaign_states",
        expected=expected_campaign_states(),
        source_label="campaign_runner statuses",
    )
    _validate_state_vocabulary(
        payload,
        errors,
        field="all_lanes_terminal_states",
        expected=expected_all_lanes_terminal_states(),
        source_label="all_lanes.TERMINAL_STATES",
    )
    _validate_state_vocabulary(
        payload,
        errors,
        field="artifact_states",
        expected=expected_artifact_states(),
        source_label="evidence_snapshot artifact statuses",
    )
    _validate_state_vocabulary(
        payload,
        errors,
        field="artifact_coverage_states",
        expected=expected_artifact_coverage_states(),
        source_label="evidence_snapshot artifact coverage statuses",
    )
    _validate_state_vocabulary(
        payload,
        errors,
        field="artifact_stage_states",
        expected=expected_artifact_stage_states(),
        source_label="evidence_snapshot artifact stage statuses",
    )
    _validate_state_vocabulary(
        payload,
        errors,
        field="data_states",
        expected=expected_data_states(),
        source_label="event row and data coverage statuses",
    )
    _validate_state_vocabulary(
        payload,
        errors,
        field="event_row_states",
        expected=expected_event_row_states(),
        source_label="economic_event_universe VALID_ROW_STATUSES",
    )
    _validate_state_vocabulary(
        payload,
        errors,
        field="data_coverage_states",
        expected=expected_data_coverage_states(),
        source_label="coverage_check._coverage_status",
    )
    _validate_state_vocabulary(
        payload,
        errors,
        field="robustness_states",
        expected=expected_robustness_states(),
        source_label="campaign_runner/WFC/robustness emitted statuses",
    )
    _validate_state_vocabulary(
        payload,
        errors,
        field="sim_shadow_states",
        expected=expected_sim_shadow_states(),
        source_label="campaign_runner SIM_SHADOW_STATUSES",
    )
    _validate_state_vocabulary(
        payload,
        errors,
        field="cme_lane_states",
        expected=expected_rithmic_endpoint_states(),
        source_label="Rithmic endpoint statuses",
    )
    _validate_state_vocabulary(
        payload,
        errors,
        field="rithmic_order_ack_states",
        expected=expected_rithmic_order_ack_states(),
        source_label="Rithmic order-ack statuses",
    )
    endpoints = payload.get("backend_endpoints")
    workbench_commands = _workbench_cli_subcommands()
    dispatched_commands = _workbench_cli_dispatch_commands()
    expected_cli_args = _workbench_cli_request_args()
    endpoint_commands: set[str] = set()
    if not isinstance(endpoints, list) or not endpoints:
        errors.append("backend_endpoints must be a non-empty list")
    else:
        endpoint_ids = [
            str(endpoint.get("id") or "")
            for endpoint in endpoints
            if isinstance(endpoint, dict) and endpoint.get("id")
        ]
        if len(endpoint_ids) != len(set(endpoint_ids)):
            errors.append("backend endpoint ids must be unique")
        for index, endpoint in enumerate(endpoints):
            if not isinstance(endpoint, dict):
                continue
            cli = endpoint.get("cli")
            command = _workbench_cli_command(cli)
            if command is None:
                errors.append(
                    f"backend_endpoints[{index}].cli must be 'python -m workbench <subcommand>', got {cli!r}"
                )
            elif command not in workbench_commands:
                errors.append(
                    f"backend_endpoints[{index}].cli references unknown Workbench CLI subcommand: {command!r}"
                )
            elif command not in dispatched_commands:
                errors.append(
                    f"backend_endpoints[{index}].cli references undispatched Workbench CLI subcommand: {command!r}"
                )
            elif command in UTILITY_CLI_COMMAND_SCOPES:
                errors.append(
                    f"backend_endpoints[{index}].cli references utility-only Workbench CLI subcommand: {command!r}"
                )
            else:
                endpoint_commands.add(command)
                _validate_cli_request_args(
                    errors,
                    collection_name="backend_endpoints",
                    index=index,
                    entry=endpoint,
                    command=command,
                    expected_by_command=expected_cli_args,
                )
                if command == "download":
                    _validate_download_response_contract(errors, index, endpoint)
    utilities = payload.get("utility_cli_commands")
    utility_commands: set[str] = set()
    if not isinstance(utilities, list) or not utilities:
        errors.append("utility_cli_commands must be a non-empty list")
    else:
        utility_ids = [
            str(utility.get("id") or "")
            for utility in utilities
            if isinstance(utility, dict) and utility.get("id")
        ]
        if len(utility_ids) != len(set(utility_ids)):
            errors.append("utility CLI command ids must be unique")
        for index, utility in enumerate(utilities):
            if not isinstance(utility, dict):
                continue
            cli = utility.get("cli")
            command = _workbench_cli_command(cli)
            if command is None:
                errors.append(
                    f"utility_cli_commands[{index}].cli must be 'python -m workbench <subcommand>', got {cli!r}"
                )
            elif command in endpoint_commands:
                errors.append(
                    f"utility_cli_commands[{index}].cli duplicates backend endpoint subcommand: {command!r}"
                )
            elif command not in workbench_commands:
                errors.append(
                    f"utility_cli_commands[{index}].cli references unknown Workbench CLI subcommand: {command!r}"
                )
            elif command not in dispatched_commands:
                errors.append(
                    f"utility_cli_commands[{index}].cli references undispatched Workbench CLI subcommand: {command!r}"
                )
            elif utility.get("utility_scope") != UTILITY_CLI_COMMAND_SCOPES.get(command):
                errors.append(
                    "utility_cli_commands[{index}].utility_scope must be {expected!r} for {command!r}".format(
                        index=index,
                        expected=UTILITY_CLI_COMMAND_SCOPES.get(command),
                        command=command,
                    )
                )
            else:
                utility_commands.add(command)
                _validate_cli_request_args(
                    errors,
                    collection_name="utility_cli_commands",
                    index=index,
                    entry=utility,
                    command=command,
                    expected_by_command=expected_cli_args,
                )
    uncovered_commands = sorted((workbench_commands | dispatched_commands) - endpoint_commands - utility_commands)
    if uncovered_commands:
        errors.append(f"Workbench CLI subcommands missing runtime contract coverage: {uncovered_commands!r}")
    return errors
