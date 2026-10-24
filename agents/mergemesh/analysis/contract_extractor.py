"""Deterministic REST JSON contract extractor and comparator."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from domain.models import ContractComparison, ContractFieldChange, ContractFieldInfo


class ContractAnalysisError(ValueError):
    """Raised when contract fixture data is missing, malformed, or unsupported."""


def _json_type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _normalize_identifier(name: str) -> str:
    """Normalize snake_case and camelCase field names for heuristic rename matching."""
    leaf = name.split(".")[-1]
    return re.sub(r"[_\-\s]", "", leaf).lower()


def _extract_fields_recursive(
    obj: dict[str, Any],
    source_path: str,
    required_set: set[str],
    prefix: str = "",
) -> dict[str, ContractFieldInfo]:
    fields: dict[str, ContractFieldInfo] = {}
    for key, val in obj.items():
        if not isinstance(key, str):
            continue
        path = f"{prefix}.{key}" if prefix else key
        jtype = _json_type_name(val)
        fields[path] = ContractFieldInfo(
            field_path=path,
            json_type=jtype,
            sample_value=val if not isinstance(val, (dict, list)) else f"<{jtype}>",
            source_path=source_path,
            required=(path in required_set or key in required_set),
        )
        if isinstance(val, dict):
            nested = _extract_fields_recursive(val, source_path, required_set, prefix=path)
            fields.update(nested)
    return fields


def load_contract_json(file_path: Path) -> tuple[dict[str, Any], set[str]]:
    """Load and validate a JSON contract snapshot or minimal schema object."""
    if not file_path.exists() or not file_path.is_file():
        raise ContractAnalysisError(f"Contract file not found: {file_path}")

    try:
        raw_text = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ContractAnalysisError(f"Unable to read contract file {file_path}: {exc}") from exc

    if not raw_text.strip():
        raise ContractAnalysisError(f"Contract file is empty: {file_path}")

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ContractAnalysisError(
            f"Malformed JSON in contract file {file_path.name} (line {exc.lineno}): {exc.msg}"
        ) from exc

    if not isinstance(parsed, dict):
        raise ContractAnalysisError(
            f"Unsupported contract root type '{_json_type_name(parsed)}' in {file_path.name}; expected JSON object."
        )

    explicit_required: set[str] = set()
    if "$schema" in parsed or ("properties" in parsed and isinstance(parsed.get("properties"), dict)):
        req_list = parsed.get("required", [])
        if isinstance(req_list, list):
            explicit_required.update(str(r) for r in req_list)
        props = parsed.get("properties", {})
        sample_obj: dict[str, Any] = {}
        for k, v in props.items():
            if isinstance(v, dict) and "type" in v:
                t = v["type"]
                sample_obj[k] = v.get(
                    "example",
                    "" if t == "string" else (0 if t in ("integer", "number") else (False if t == "boolean" else {})),
                )
            else:
                sample_obj[k] = v
        return sample_obj, explicit_required

    return parsed, explicit_required


def compare_contracts(
    before_data: dict[str, Any],
    after_data: dict[str, Any],
    before_path: str = "contracts/provider_before.json",
    after_path: str = "contracts/provider_after.json",
    required_fields: list[str] | None = None,
) -> ContractComparison:
    """Compare before and after REST JSON contracts deterministically."""
    if not isinstance(before_data, dict) or not isinstance(after_data, dict):
        raise ContractAnalysisError("Both before and after contracts must be JSON objects (dicts).")

    req_set = set(required_fields or [])
    before_fields = _extract_fields_recursive(before_data, before_path, req_set)
    after_fields = _extract_fields_recursive(after_data, after_path, req_set)

    before_keys = set(before_fields.keys())
    after_keys = set(after_fields.keys())

    removed = sorted(before_keys - after_keys)
    added = sorted(after_keys - before_keys)
    common = sorted(before_keys & after_keys)

    # Identify candidate renames among removed and added fields
    rename_candidates: dict[str, str] = {}
    used_added: set[str] = set()
    for rem_key in removed:
        rem_info = before_fields[rem_key]
        rem_norm = _normalize_identifier(rem_key)
        rem_parent = ".".join(rem_key.split(".")[:-1])

        best_match: str | None = None
        for add_key in added:
            if add_key in used_added:
                continue
            add_info = after_fields[add_key]
            add_parent = ".".join(add_key.split(".")[:-1])
            if rem_parent != add_parent:
                continue
            add_norm = _normalize_identifier(add_key)
            # Match by normalized identifier (e.g., customer_id -> customerId) or identical non-empty sample value + type
            if rem_norm == add_norm and rem_norm != "":
                best_match = add_key
                break
            if (
                rem_info.json_type == add_info.json_type
                and rem_info.sample_value is not None
                and rem_info.sample_value != ""
                and rem_info.sample_value == add_info.sample_value
            ):
                best_match = add_key
                break

        if best_match:
            rename_candidates[rem_key] = best_match
            used_added.add(best_match)

    type_changes: list[ContractFieldChange] = []
    field_changes: list[ContractFieldChange] = []
    missing_required: list[str] = []

    for r_field in req_set:
        if r_field not in after_fields:
            missing_required.append(r_field)
    missing_required = sorted(set(missing_required))

    for key in sorted(before_keys | after_keys):
        b_info = before_fields.get(key)
        a_info = after_fields.get(key)
        is_req = key in req_set or (b_info.required if b_info else False)

        if b_info and not a_info:
            candidate = rename_candidates.get(key)
            impact = (
                f"BREAKING CANDIDATE: Field removed (candidate rename to '{candidate}')"
                if candidate
                else ("BREAKING: Required field removed" if is_req else "BREAKING CANDIDATE: Field removed")
            )
            field_changes.append(
                ContractFieldChange(
                    field_path=key,
                    change_type="removed",
                    before_type=b_info.json_type,
                    after_type=None,
                    before_value=b_info.sample_value,
                    after_value=None,
                    replacement_candidate=candidate,
                    required=is_req,
                    impact=impact,
                )
            )
        elif a_info and not b_info:
            reverse_rename = next((old for old, new in rename_candidates.items() if new == key), None)
            impact = (
                f"Added field (candidate replacement for '{reverse_rename}')"
                if reverse_rename
                else "Compatible addition (non-breaking)"
            )
            field_changes.append(
                ContractFieldChange(
                    field_path=key,
                    change_type="added",
                    before_type=None,
                    after_type=a_info.json_type,
                    before_value=None,
                    after_value=a_info.sample_value,
                    replacement_candidate=None,
                    required=is_req,
                    impact=impact,
                )
            )
        elif b_info and a_info:
            if b_info.json_type != a_info.json_type:
                change = ContractFieldChange(
                    field_path=key,
                    change_type="type_changed",
                    before_type=b_info.json_type,
                    after_type=a_info.json_type,
                    before_value=b_info.sample_value,
                    after_value=a_info.sample_value,
                    required=is_req,
                    impact=f"BREAKING: Incompatible type change ({b_info.json_type} -> {a_info.json_type})",
                )
                type_changes.append(change)
                field_changes.append(change)
            else:
                field_changes.append(
                    ContractFieldChange(
                        field_path=key,
                        change_type="unchanged",
                        before_type=b_info.json_type,
                        after_type=a_info.json_type,
                        before_value=b_info.sample_value,
                        after_value=a_info.sample_value,
                        required=is_req,
                        impact="Unchanged (compatible)",
                    )
                )

    for req_missing in missing_required:
        if req_missing not in before_keys and req_missing not in after_keys:
            field_changes.append(
                ContractFieldChange(
                    field_path=req_missing,
                    change_type="missing_required",
                    before_type="required",
                    after_type=None,
                    required=True,
                    impact="BREAKING: Required field missing from provider contract",
                )
            )

    is_breaking = bool(removed or type_changes or missing_required)

    return ContractComparison(
        provider_before_path=before_path,
        provider_after_path=after_path,
        before_fields=before_fields,
        after_fields=after_fields,
        removed_fields=removed,
        added_fields=added,
        type_changes=type_changes,
        missing_required_fields=missing_required,
        rename_candidates=rename_candidates,
        field_changes=field_changes,
        is_breaking_candidate=is_breaking,
    )


def compare_contract_files(
    before_file: Path,
    after_file: Path,
    before_rel_path: str = "contracts/provider_before.json",
    after_rel_path: str = "contracts/provider_after.json",
    required_fields: list[str] | None = None,
) -> ContractComparison:
    """Load two JSON contract files from disk and compare them."""
    before_data, before_req = load_contract_json(before_file)
    after_data, after_req = load_contract_json(after_file)
    combined_req = sorted(set(required_fields or []) | before_req | after_req)
    return compare_contracts(
        before_data=before_data,
        after_data=after_data,
        before_path=before_rel_path,
        after_path=after_rel_path,
        required_fields=combined_req,
    )
