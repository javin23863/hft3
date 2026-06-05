"""Machine-readable Workbench runtime contract."""

from __future__ import annotations

import ast
import importlib
import json
import shlex
from pathlib import Path
from typing import Any

CONTRACT_PATH = Path(__file__).resolve().parents[1] / "config" / "runtime_contract.json"
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "runtime_contract.schema.json"
WORKBENCH_MAIN_PATH = Path(__file__).resolve().parents[1] / "__main__.py"
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


def _workbench_cli_subcommands(path: Path = WORKBENCH_MAIN_PATH) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
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
    return errors
