from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field


class ModuleDescriptor(BaseModel):
    """Stable metadata used by an orchestrator to discover a plug-in module."""

    module_id: str
    display_name: str
    version: str = "1.0.0"
    description: str
    input_model: str
    output_model: str = "cad_ai.manufacturing_modules.contracts.ModuleResult"
    draft_only: bool = True
    side_effects: list[str] = Field(default_factory=list)


class ModuleResult(BaseModel):
    """Common envelope returned by every independently insertable module."""

    module_id: str
    module_version: str
    status: str
    data: dict[str, Any] = Field(default_factory=dict)
    artifacts: dict[str, str] = Field(default_factory=dict)
    open_questions: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    guardrails: dict[str, Any] = Field(default_factory=dict)
    trace: dict[str, Any] = Field(default_factory=dict)


class ManufacturingModule(Protocol):
    descriptor: ModuleDescriptor

    def execute(self, payload: BaseModel | dict[str, Any]) -> ModuleResult: ...


class ManufacturingModuleRegistry:
    """Small dependency-free registry suitable for later workflow insertion."""

    def __init__(self) -> None:
        self._modules: dict[str, ManufacturingModule] = {}

    def register(self, module: ManufacturingModule, *, replace: bool = False) -> None:
        module_id = module.descriptor.module_id
        if module_id in self._modules and not replace:
            raise ValueError(f"module already registered: {module_id}")
        self._modules[module_id] = module

    def get(self, module_id: str) -> ManufacturingModule:
        try:
            return self._modules[module_id]
        except KeyError as exc:
            raise KeyError(f"unknown manufacturing module: {module_id}") from exc

    def execute(self, module_id: str, payload: BaseModel | dict[str, Any]) -> ModuleResult:
        return self.get(module_id).execute(payload)

    def catalog(self) -> list[dict[str, Any]]:
        return [
            self._modules[module_id].descriptor.model_dump(mode="json")
            for module_id in sorted(self._modules)
        ]
