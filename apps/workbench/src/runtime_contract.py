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


def allowed_runtime_state_refs() -> set[str]:
    snapshot_refs = {f"RunEvidenceSnapshot.{field}" for field in _run_evidence_snapshot_fields()}
    return snapshot_refs | RUNTIME_STATE_NESTED_REFS
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
    endpoints = payload.get("backend_endpoints")
    if not isinstance(endpoints, list) or not endpoints:
        errors.append("backend_endpoints must be a non-empty list")
    else:
        workbench_commands = _workbench_cli_subcommands()
        dispatched_commands = _workbench_cli_dispatch_commands()
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
    return errors
