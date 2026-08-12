"""SOP-only manufacturing module exports."""

from .contracts import ManufacturingModuleRegistry, ModuleDescriptor, ModuleResult
from .sop_drawing import SopDrawingModule, SopDrawingModuleRequest


def create_sop_module_registry() -> ManufacturingModuleRegistry:
    registry = ManufacturingModuleRegistry()
    registry.register(SopDrawingModule())
    return registry


__all__ = [
    "ManufacturingModuleRegistry",
    "ModuleDescriptor",
    "ModuleResult",
    "SopDrawingModule",
    "SopDrawingModuleRequest",
    "create_sop_module_registry",
]
