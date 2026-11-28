"""Portable role/method packages with lazy instructions and local output contracts.

runtime.json is this application's extension, not a required Agent Skills field.
"""

import json
from pathlib import Path

from jsonschema import Draft202012Validator, validate

ROOT = Path(__file__).resolve().parents[1]


class Skills:
    def __init__(self, directory=ROOT / "skills"):
        self.catalog = {}
        for path in sorted(Path(directory).glob("*/SKILL.md")):
            metadata = {}
            with path.open(encoding="utf-8") as stream:
                if stream.readline().strip() != "---":
                    continue
                for line in stream:
                    if line.strip() == "---":
                        break
                    key, separator, value = line.partition(":")
                    if separator:
                        metadata[key.strip()] = value.strip()
            runtime_path = path.parent / "runtime.json"
            runtime = (
                json.loads(runtime_path.read_text(encoding="utf-8"))
                if runtime_path.exists()
                else {"kind": "role", "output_schema": "output.schema.json"}
            )
            name = metadata["name"]
            if name in self.catalog:
                raise ValueError("Duplicate Skill name")
            self.catalog[name] = {
                **metadata,
                "path": path,
                "kind": runtime["kind"],
                "runtime": runtime,
            }
        self.active = None
        self.loaded_resources = []

    def resource(self, name, relative):
        base = self.catalog[name]["path"].parent.resolve()
        path = (base / relative).resolve()
        if not path.is_relative_to(base) or not path.is_file():
            raise ValueError("Skill resource must be a file within its package")
        self.loaded_resources.append({"skill": name, "resource": relative})
        return path.read_text(encoding="utf-8")

    def schema(self, name):
        schema = json.loads(
            self.resource(name, self.catalog[name]["runtime"]["output_schema"])
        )
        Draft202012Validator.check_schema(schema)
        return schema

    def activate(self, name, reason):
        entry = self.catalog[name]
        runtime = entry["runtime"]
        self.active = name
        bodies = []
        base = runtime.get("base_role")
        if base:
            if self.catalog[base]["kind"] != "role":
                raise ValueError("A method can inherit only a role")
            bodies.append(self.resource(base, "SKILL.md").split("---", 2)[2].strip())
        bodies.append(self.resource(name, "SKILL.md").split("---", 2)[2].strip())
        for ref in runtime.get("references", []):
            bodies.append(f"Reference {ref}:\n" + self.resource(name, ref))
        return "\n\n".join(bodies), {
            "name": name,
            "kind": entry["kind"],
            "base_role": base,
            "version": runtime.get("version", 1),
            "reason": reason,
            "allowed_tools": self.allowed_tools,
            "resources": [r for r in self.loaded_resources if r["skill"] == name][-8:],
        }

    @property
    def allowed_tools(self):
        if self.active is None:
            return []
        entry = self.catalog[self.active]
        return entry["runtime"].get("capabilities", entry.get("tools", "").split())

    def permit(self, tool):
        if tool not in self.allowed_tools:
            raise PermissionError(f"Skill {self.active} cannot call {tool}.")

    def validate(self, name, output):
        validate(output, self.schema(name))
        return output

    def methods(self):
        return [
            {
                "name": name,
                "description": entry["description"],
                "capabilities": entry["runtime"].get("capabilities", []),
            }
            for name, entry in self.catalog.items()
            if entry["kind"] == "method"
        ]


# Compatibility for evaluation clients. Contracts are maintained in packages.
SCHEMAS = {
    name: Skills().schema(name)
    for name, meta in Skills().catalog.items()
    if meta["kind"] == "role"
}
