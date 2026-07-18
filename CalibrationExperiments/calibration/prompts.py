from __future__ import annotations

import hashlib
import json
import string
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

from calibration.models import Message


class PromptRegistryError(ValueError):
    """Raised when a prompt lock or render would be ambiguous."""


@dataclass(frozen=True, slots=True)
class PromptSpec:
    prompt_id: str
    version: str
    task_family: str
    messages: tuple[dict[str, str], ...]
    conditions: tuple[str, ...]
    tools: tuple[dict[str, Any], ...] = ()
    response_format: dict[str, Any] | None = None
    supported_features: tuple[str, ...] = ()
    declared_hash: str | None = None
    content_hash: str = ""

    def __post_init__(self) -> None:
        if not self.prompt_id or not self.version or not self.task_family:
            raise PromptRegistryError("Prompt ID, version, and task family are required")
        if not self.messages or not self.conditions:
            raise PromptRegistryError("Prompts require messages and supported conditions")
        if not set(self.conditions).issuperset({"baseline"}):
            raise PromptRegistryError("Every prompt must support the baseline condition")
        for message in self.messages:
            if message.get("role") not in {"system", "user", "assistant", "tool"}:
                raise PromptRegistryError("Prompt message roles must be canonical")
            if "content" not in message:
                raise PromptRegistryError("Prompt messages require content")
        actual = _hash_spec_material(self)
        if self.declared_hash and self.declared_hash != actual:
            raise PromptRegistryError(
                f"Prompt hash mismatch for {self.prompt_id}: expected {self.declared_hash}, got {actual}"
            )
        if not self.content_hash:
            object.__setattr__(self, "content_hash", actual)

    def lock(self) -> dict[str, str]:
        return {
            "prompt_id": self.prompt_id,
            "version": self.version,
            "content_hash": self.content_hash,
        }


@dataclass(frozen=True, slots=True)
class RenderedPrompt:
    prompt_id: str
    version: str
    condition_id: str
    messages: tuple[Message, ...]
    tools: tuple[dict[str, Any], ...]
    response_format: dict[str, Any] | None
    render_hash: str
    variables: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "prompt_id": self.prompt_id,
            "version": self.version,
            "condition_id": self.condition_id,
            "messages": [asdict(message) for message in self.messages],
            "tools": list(self.tools),
            "response_format": self.response_format,
            "render_hash": self.render_hash,
            "variables": self.variables,
        }


class PromptRegistry:
    def __init__(self, prompts: Mapping[str, PromptSpec]) -> None:
        self._prompts = dict(prompts)
        if len(self._prompts) != len(set(self._prompts)):
            raise PromptRegistryError("Prompt IDs must be unique")

    @classmethod
    def from_file(cls, path: str | Path) -> "PromptRegistry":
        document = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(document, dict) or not isinstance(document.get("prompts"), list):
            raise PromptRegistryError("Prompt registry must contain a prompts list")
        prompts: list[PromptSpec] = []
        for raw in document["prompts"]:
            if not isinstance(raw, dict):
                raise PromptRegistryError("Each prompt entry must be an object")
            messages = tuple(
                {"role": str(item["role"]), "content": str(item["content"])}
                for item in raw.get("messages", [])
            )
            prompts.append(
                PromptSpec(
                    prompt_id=str(raw["prompt_id"]),
                    version=str(raw["version"]),
                    task_family=str(raw["task_family"]),
                    messages=messages,
                    conditions=tuple(str(item) for item in raw.get("conditions", [])),
                    tools=tuple(dict(item) for item in raw.get("tools", [])),
                    response_format=raw.get("response_format"),
                    supported_features=tuple(
                        str(item) for item in raw.get("supported_features", [])
                    ),
                    declared_hash=raw.get("content_hash"),
                )
            )
        return cls({prompt.prompt_id: prompt for prompt in prompts})

    def get(self, prompt_id: str) -> PromptSpec:
        try:
            return self._prompts[prompt_id]
        except KeyError as error:
            raise PromptRegistryError(f"Unknown prompt ID: {prompt_id}") from error

    def render(
        self,
        prompt_id: str,
        condition: str,
        variables: Mapping[str, Any] | None = None,
    ) -> RenderedPrompt:
        prompt = self.get(prompt_id)
        if condition not in prompt.conditions:
            raise PromptRegistryError(
                f"Prompt {prompt_id} does not support condition {condition!r}"
            )
        values = {"condition": condition, **dict(variables or {})}
        messages = tuple(
            Message(
                role=item["role"],
                content=_render_template(item["content"], values),
            )
            for item in prompt.messages
        )
        tools = tuple(_render_object(tool, values) for tool in prompt.tools)
        response_format = (
            None if prompt.response_format is None else _render_object(prompt.response_format, values)
        )
        material = {
            "prompt_id": prompt.prompt_id,
            "version": prompt.version,
            "condition_id": condition,
            "messages": [asdict(message) for message in messages],
            "tools": list(tools),
            "response_format": response_format,
        }
        render_hash = hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
                "utf-8"
            )
        ).hexdigest()
        return RenderedPrompt(
            prompt_id=prompt.prompt_id,
            version=prompt.version,
            condition_id=condition,
            messages=messages,
            tools=tools,
            response_format=response_format,
            render_hash=render_hash,
            variables=values,
        )


def _hash_spec_material(prompt: PromptSpec) -> str:
    material = {
        "prompt_id": prompt.prompt_id,
        "version": prompt.version,
        "task_family": prompt.task_family,
        "messages": list(prompt.messages),
        "conditions": list(prompt.conditions),
        "tools": list(prompt.tools),
        "response_format": prompt.response_format,
        "supported_features": list(prompt.supported_features),
    }
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
    ).hexdigest()


def _render_template(template: str, values: Mapping[str, Any]) -> str:
    fields = {
        field_name
        for _, field_name, _, _ in string.Formatter().parse(template)
        if field_name
    }
    missing = sorted(fields - values.keys())
    if missing:
        raise PromptRegistryError(f"Missing prompt variables: {missing}")
    try:
        return template.format_map(_StrictVariables(values))
    except (KeyError, ValueError, IndexError) as error:
        raise PromptRegistryError(f"Invalid prompt template: {error}") from error


def _render_object(value: Any, variables: Mapping[str, Any]) -> Any:
    if isinstance(value, str):
        return _render_template(value, variables)
    if isinstance(value, list):
        return [_render_object(item, variables) for item in value]
    if isinstance(value, dict):
        return {key: _render_object(value[key], variables) for key in sorted(value)}
    return value


class _StrictVariables(dict[str, Any]):
    def __missing__(self, key: str) -> Any:
        raise PromptRegistryError(f"Missing prompt variable: {key}")
