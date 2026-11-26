"""Profile and project-config loading.

A *profile* is a JSON document of scanning rules (ID pattern, context words,
skip lists, path patterns). Profiles may ``extends`` another profile; the child
is merged over the parent — lists are concatenated (parent first, deduplicated),
scalars and dicts are overridden key-by-key.

A *project config* (``.data-compliance.json`` at a project root) selects a
profile and layers project-specific overrides on top, so per-repo tweaks live
with the repo rather than in the toolkit.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

PROFILE_DIR = Path(__file__).resolve().parent / "profiles"
PROJECT_CONFIG_NAMES = (".data-compliance.json", ".data-compliance.local.json")

# Keys whose list values accumulate across extends/overrides rather than replace.
_MERGE_LISTS = {
    "positive_context", "negative_context", "skip_ext", "skip_dirs", "skip_names",
    "path_patterns", "path_pattern_allow", "sensitive_filenames", "bulk_data_ext",
    "public_allowlist", "aggregate_allowlist", "scratch_locations", "checkpoint_dirs",
    "vendor_dirs",
}


class ProfileError(RuntimeError):
    pass


def _merge(parent: Dict[str, Any], child: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(parent)
    for key, val in child.items():
        if key in _MERGE_LISTS and isinstance(val, list):
            base = out.get(key) or []
            combined = list(base) + [v for v in val if v not in base]
            out[key] = combined
        elif isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], val)
        else:
            out[key] = val
    return out


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        raise ProfileError(f"no such profile/config: {path}")
    except json.JSONDecodeError as exc:
        raise ProfileError(f"{path}: invalid JSON: {exc}")


def _resolve_profile_path(name_or_path: str) -> Path:
    p = Path(name_or_path)
    if p.suffix == ".json" and p.exists():
        return p
    candidate = PROFILE_DIR / f"{name_or_path}.json"
    if candidate.exists():
        return candidate
    raise ProfileError(
        f"unknown profile {name_or_path!r}; available: {', '.join(available_profiles())}"
    )


def available_profiles() -> List[str]:
    if not PROFILE_DIR.is_dir():
        return []
    return sorted(p.stem for p in PROFILE_DIR.glob("*.json"))


def _load_raw(name_or_path: str, _seen: Optional[set] = None) -> Dict[str, Any]:
    _seen = _seen or set()
    path = _resolve_profile_path(name_or_path)
    key = str(path.resolve())
    if key in _seen:
        raise ProfileError(f"circular profile extends chain at {path}")
    _seen.add(key)
    data = _read_json(path)
    parent_name = data.pop("extends", None)
    if parent_name:
        return _merge(_load_raw(parent_name, _seen), data)
    return data


@dataclass
class PathPattern:
    name: str
    regex: "re.Pattern"
    severity: str
    hint: str


@dataclass
class NameRule:
    regex: "re.Pattern"
    severity: str
    note: str


@dataclass
class Profile:
    """Compiled, ready-to-use scanning rules."""

    name: str
    description: str
    id_label: str
    id_long_label: str
    id_regex: "re.Pattern"
    id_min: int
    id_max: int
    id_column_names: set
    id_column_prefixes: tuple
    id_allow_values: frozenset
    positive_context: tuple
    negative_context: tuple
    skip_ext: set
    skip_dirs: set
    skip_names: set
    vendor_dirs: set
    max_text_mb: float
    max_pkl_mb: float
    csv_sample_rows: int
    path_patterns: List[PathPattern] = field(default_factory=list)
    path_pattern_allow: tuple = ()
    sensitive_filenames: List[NameRule] = field(default_factory=list)
    bulk_data_ext: set = field(default_factory=set)
    bulk_data_mb: float = 5.0
    # A table / array / sequence with at least this many rows is treated as
    # per-participant (or per-interval) by the KEEP content check.
    individual_rows: int = 5000
    # A text file holding at least this many distinct identifier values, on a
    # large share of its lines, is an ID list or per-participant dump, not
    # documentation (the KEEP content check for text files).
    id_list_min: int = 50
    raw: Dict[str, Any] = field(default_factory=dict)

    # -- helpers used by the scanners -------------------------------------
    def is_id_column(self, name: str) -> bool:
        n = str(name).strip().strip("\"'").lower()
        if n in self.id_column_names:
            return True
        return any(n.startswith(p) for p in self.id_column_prefixes)

    def in_range(self, value: int) -> bool:
        return self.id_min <= value <= self.id_max

    def allowed(self, text: str) -> bool:
        """True for literal values declared as known-safe examples.

        Narrow by design: an allowlist is a hole in the detector, so it holds
        exact strings a project has justified in its own config, never patterns.
        """
        return text in self.id_allow_values

    def context_excludes(self, snippet: str) -> bool:
        s = snippet.lower()
        return any(neg in s for neg in self.negative_context)

    def context_supports(self, snippet: str) -> bool:
        s = snippet.lower()
        return any(pos in s for pos in self.positive_context)


def _compile(data: Dict[str, Any]) -> Profile:
    idc = data.get("id", {})
    try:
        id_regex = re.compile(idc["regex"])
    except KeyError:
        raise ProfileError("profile is missing id.regex")
    except re.error as exc:
        raise ProfileError(f"invalid id.regex: {exc}")

    patterns = []
    for spec in data.get("path_patterns", []):
        try:
            patterns.append(
                PathPattern(
                    name=spec["name"],
                    regex=re.compile(spec["regex"]),
                    severity=spec.get("severity", "MEDIUM").upper(),
                    hint=spec.get("hint", ""),
                )
            )
        except (KeyError, re.error) as exc:
            raise ProfileError(f"bad path_pattern {spec!r}: {exc}")

    names = []
    for spec in data.get("sensitive_filenames", []):
        try:
            names.append(
                NameRule(
                    regex=re.compile(spec["regex"]),
                    severity=spec.get("severity", "MEDIUM").upper(),
                    note=spec.get("note", ""),
                )
            )
        except (KeyError, re.error) as exc:
            raise ProfileError(f"bad sensitive_filename {spec!r}: {exc}")

    return Profile(
        name=data.get("name", "unnamed"),
        description=data.get("description", ""),
        id_label=idc.get("label", "id"),
        id_long_label=idc.get("long_label", idc.get("label", "identifier")),
        id_regex=id_regex,
        id_min=int(idc.get("min", 0)),
        id_max=int(idc.get("max", 10 ** 12)),
        id_column_names={c.lower() for c in idc.get("column_names", [])},
        id_column_prefixes=tuple(p.lower() for p in idc.get("column_prefixes", [])),
        id_allow_values=frozenset(str(v) for v in idc.get("allow_values", [])),
        positive_context=tuple(s.lower() for s in data.get("positive_context", [])),
        negative_context=tuple(s.lower() for s in data.get("negative_context", [])),
        skip_ext={e.lower() for e in data.get("skip_ext", [])},
        skip_dirs=set(data.get("skip_dirs", [])),
        skip_names=set(data.get("skip_names", [])),
        vendor_dirs=set(data.get("vendor_dirs", [])),
        max_text_mb=float(data.get("max_text_mb", 50)),
        max_pkl_mb=float(data.get("max_pkl_mb", 200)),
        csv_sample_rows=int(data.get("csv_sample_rows", 2000)),
        path_patterns=patterns,
        path_pattern_allow=tuple(data.get("path_pattern_allow", [])),
        sensitive_filenames=names,
        bulk_data_ext={e.lower() for e in data.get("bulk_data_ext", [])},
        bulk_data_mb=float(data.get("bulk_data_mb", 5)),
        individual_rows=int(data.get("individual_rows", 5000)),
        id_list_min=int(data.get("id_list_min", 50)),
        raw=data,
    )


def find_project_config(start: Path) -> Optional[Path]:
    """Walk up from *start* looking for a project config file."""
    start = start.resolve()
    for d in [start] + list(start.parents):
        for name in PROJECT_CONFIG_NAMES:
            candidate = d / name
            if candidate.is_file():
                return candidate
    return None


def load_profile(
    name: Optional[str] = None,
    project_root: Optional[Path] = None,
    overrides: Optional[Dict[str, Any]] = None,
) -> Profile:
    """Load a profile, layering project config and explicit overrides on top.

    Precedence (low to high): profile file (+ its ``extends`` chain),
    project config, ``overrides``.
    """
    config: Dict[str, Any] = {}
    config_path = find_project_config(project_root or Path.cwd()) if project_root is not False else None
    if config_path:
        config = _read_json(config_path)

    profile_name = name or config.get("profile") or "generic"
    data = _load_raw(profile_name)

    project_rules = {k: v for k, v in config.items() if k != "profile"}
    if project_rules:
        data = _merge(data, project_rules)
    if overrides:
        data = _merge(data, overrides)

    profile = _compile(data)
    profile.raw["_config_path"] = str(config_path) if config_path else None
    return profile
