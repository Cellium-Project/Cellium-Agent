# -*- coding: utf-8 -*-
"""
Test component hot reload behavior for class variables vs instance variables
"""

import importlib.util
import os
import shutil
import sys
from pathlib import Path

import pytest


def _load_module_from_file(file_path: str, module_name: str):
    """Load a Python module from file path"""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class TestComponentStateReload:
    """Component state hot reload tests"""

    def test_class_variable_persists_after_instance_recreate(self, tmp_path):
        """Test that class variables persist after creating new instance"""
        components_dir = tmp_path / "components"
        components_dir.mkdir()
        (components_dir / "__init__.py").write_text("")
        
        component_code = '''
# -*- coding: utf-8 -*-
class TestStateComponent:
    cell_name = "test_state"
    
    # Class variable
    class_config = "initial_class"
    class_cache = {"value": "initial_class"}
    
    def __init__(self):
        # Instance variable
        self.instance_config = "initial_instance"
        self.instance_cache = {"value": "initial_instance"}
    
    def get_class_var(self):
        return {"class_config": self.class_config, "class_cache": self.class_cache}
    
    def get_instance_var(self):
        return {"instance_config": self.instance_config, "instance_cache": self.instance_cache}
    
    def set_class_var(self, value):
        self.__class__.class_config = value
        self.__class__.class_cache["value"] = value
        return {"success": True, "new_value": value}
    
    def set_instance_var(self, value):
        self.instance_config = value
        self.instance_cache["value"] = value
        return {"success": True, "new_value": value}
'''
        component_file = components_dir / "test_state_component.py"
        component_file.write_text(component_code, encoding="utf-8")
        
        module_name = "test_state_component_unique_1"
        
        try:
            module = _load_module_from_file(str(component_file), module_name)
            TestStateComponent = getattr(module, "TestStateComponent")
            
            instance1 = TestStateComponent()
            
            result1 = instance1.get_class_var()
            assert result1["class_config"] == "initial_class"
            
            instance1.set_class_var("modified_class")
            
            result2 = instance1.get_class_var()
            assert result2["class_config"] == "modified_class"
            
            instance2 = TestStateComponent()
            result3 = instance2.get_class_var()
            
            print(f"\n[Class Variable Test]")
            print(f"  After creating new instance: {result3['class_config']}")
            print(f"  Expected: modified_class (class variable persists)")
            
            assert result3["class_config"] == "modified_class", \
                "Class variable should persist after creating new instance"
            
        finally:
            if module_name in sys.modules:
                del sys.modules[module_name]

    def test_instance_variable_resets_on_new_instance(self, tmp_path):
        """Test that instance variables reset in new instance"""
        components_dir = tmp_path / "components"
        components_dir.mkdir()
        (components_dir / "__init__.py").write_text("")
        
        component_code = '''
# -*- coding: utf-8 -*-
class TestStateComponent:
    cell_name = "test_state"
    
    # Class variable
    class_config = "initial_class"
    class_cache = {"value": "initial_class"}
    
    def __init__(self):
        # Instance variable
        self.instance_config = "initial_instance"
        self.instance_cache = {"value": "initial_instance"}
    
    def get_class_var(self):
        return {"class_config": self.class_config, "class_cache": self.class_cache}
    
    def get_instance_var(self):
        return {"instance_config": self.instance_config, "instance_cache": self.instance_cache}
    
    def set_class_var(self, value):
        self.__class__.class_config = value
        self.__class__.class_cache["value"] = value
        return {"success": True, "new_value": value}
    
    def set_instance_var(self, value):
        self.instance_config = value
        self.instance_cache["value"] = value
        return {"success": True, "new_value": value}
'''
        component_file = components_dir / "test_state_component.py"
        component_file.write_text(component_code, encoding="utf-8")
        
        module_name = "test_state_component_unique_2"
        
        try:
            module = _load_module_from_file(str(component_file), module_name)
            TestStateComponent = getattr(module, "TestStateComponent")
            
            instance1 = TestStateComponent()
            
            result1 = instance1.get_instance_var()
            assert result1["instance_config"] == "initial_instance"
            
            instance1.set_instance_var("modified_instance")
            
            result2 = instance1.get_instance_var()
            assert result2["instance_config"] == "modified_instance"
            
            instance2 = TestStateComponent()
            result3 = instance2.get_instance_var()
            
            print(f"\n[Instance Variable Test]")
            print(f"  After creating new instance: {result3['instance_config']}")
            print(f"  Expected: initial_instance (instance variable resets)")
            
            assert result3["instance_config"] == "initial_instance", \
                "Instance variable should reset in new instance"
            
        finally:
            if module_name in sys.modules:
                del sys.modules[module_name]

    def test_module_reload_clears_class_variables(self, tmp_path):
        """Test that reloading module clears class variables"""
        components_dir = tmp_path / "components"
        components_dir.mkdir()
        (components_dir / "__init__.py").write_text("")
        
        component_code_v1 = '''
# -*- coding: utf-8 -*-
class TestStateComponent:
    cell_name = "test_state"
    class_config = "v1_class"
    
    def __init__(self):
        self.instance_config = "v1_instance"
    
    def get_class_var(self):
        return {"class_config": self.class_config}
    
    def get_instance_var(self):
        return {"instance_config": self.instance_config}
    
    def set_class_var(self, value):
        self.__class__.class_config = value
        return {"success": True}
'''
        component_file = components_dir / "test_state_component.py"
        component_file.write_text(component_code_v1, encoding="utf-8")
        
        module_name = "test_state_component_unique_3"
        
        try:
            module = _load_module_from_file(str(component_file), module_name)
            TestStateComponent = getattr(module, "TestStateComponent")
            
            instance1 = TestStateComponent()
            result1 = instance1.get_class_var()
            print(f"\n[Module Reload Test]")
            print(f"  First load class variable: {result1['class_config']}")
            assert result1["class_config"] == "v1_class"
            
            instance1.set_class_var("modified_v1")
            result2 = instance1.get_class_var()
            print(f"  After modification: {result2['class_config']}")
            assert result2["class_config"] == "modified_v1"
            
            del sys.modules[module_name]
            
            pycache_dir = components_dir / "__pycache__"
            if pycache_dir.exists():
                pyc_files = list(pycache_dir.glob("*.pyc"))
                print(f"  Found {len(pyc_files)} .pyc files before cleanup")
                for pyc in pyc_files:
                    pyc.unlink()
                    print(f"  Deleted: {pyc.name}")
            
            component_code_v2 = '''
# -*- coding: utf-8 -*-
class TestStateComponent:
    cell_name = "test_state"
    class_config = "v2_class"
    
    def __init__(self):
        self.instance_config = "v2_instance"
    
    def get_class_var(self):
        return {"class_config": self.class_config}
    
    def get_instance_var(self):
        return {"instance_config": self.instance_config}
    
    def set_class_var(self, value):
        self.__class__.class_config = value
        return {"success": True}
'''
            component_file.write_text(component_code_v2, encoding="utf-8")
            
            module = _load_module_from_file(str(component_file), module_name)
            TestStateComponent = getattr(module, "TestStateComponent")
            
            instance2 = TestStateComponent()
            result3 = instance2.get_class_var()
            print(f"  After module reload: {result3['class_config']}")
            
            result4 = instance2.get_instance_var()
            print(f"  Instance variable after reload: {result4['instance_config']}")
            
            print(f"\n  Conclusion:")
            print(f"    - Class variable: {result3['class_config']} (should be v2_class after reload)")
            print(f"    - Instance variable: {result4['instance_config']} (should be v2_instance)")
            
            assert result3["class_config"] == "v2_class", \
                "Class variable should use new module value after reload"
            assert result4["instance_config"] == "v2_instance", \
                "Instance variable should use new module value"
            
        finally:
            if module_name in sys.modules:
                del sys.modules[module_name]

    def test_sandbox_reload_resets_state(self, tmp_path):
        """Test that sandbox reload resets component state"""
        from app.core.util.component_sandbox import ComponentSandbox
        
        components_dir = tmp_path / "components"
        components_dir.mkdir()
        (components_dir / "__init__.py").write_text("")
        
        component_code = '''
# -*- coding: utf-8 -*-
from app.core.interface.base_cell import BaseCell

class TestSandboxComponent(BaseCell):
    cell_name = "test_sandbox"
    
    def __init__(self):
        super().__init__()
        self.call_count = 0
    
    def _cmd_increment(self) -> dict:
        """Increment counter"""
        self.call_count += 1
        return {"count": self.call_count}
    
    def _cmd_get_count(self) -> dict:
        """Get counter"""
        return {"count": self.call_count}
'''
        component_file = components_dir / "test_sandbox_component.py"
        component_file.write_text(component_code, encoding="utf-8")
        
        sandbox = ComponentSandbox.get_sandbox("test_sandbox")
        
        try:
            sandbox.initialize(str(component_file), "TestSandboxComponent")
            
            result1 = sandbox.execute("increment")
            print(f"\n[Sandbox Reload Test]")
            print(f"  First increment: {result1['count']}")
            assert result1["count"] == 1
            
            result2 = sandbox.execute("increment")
            print(f"  Second increment: {result2['count']}")
            assert result2["count"] == 2
            
            sandbox2 = ComponentSandbox.reload_sandbox("test_sandbox")
            sandbox2.initialize(str(component_file), "TestSandboxComponent")
            
            result3 = sandbox2.execute("get_count")
            print(f"  After sandbox reload: {result3['count']}")
            
            print(f"\n  Conclusion:")
            print(f"    - Before reload: count = 2")
            print(f"    - After reload: count = {result3['count']} (should be 0)")
            
            assert result3["count"] == 0, \
                "Sandbox reload should reset instance state"
            
        finally:
            ComponentSandbox.remove_sandbox("test_sandbox")

    def test_sandbox_has_cell_name_attribute(self, tmp_path):
        """Test that ComponentSandbox has cell_name attribute for hot reload compatibility"""
        from app.core.util.component_sandbox import ComponentSandbox
        
        components_dir = tmp_path / "components"
        components_dir.mkdir()
        (components_dir / "__init__.py").write_text("")
        
        component_code = '''
# -*- coding: utf-8 -*-
from app.core.interface.base_cell import BaseCell

class TestCellNameComponent(BaseCell):
    cell_name = "test_cell_name"
    
    def _cmd_test(self) -> dict:
        """Test command"""
        return {"success": True}
'''
        component_file = components_dir / "test_cell_name_component.py"
        component_file.write_text(component_code, encoding="utf-8")
        
        sandbox = ComponentSandbox.get_sandbox("test_cell_name")
        
        try:
            sandbox.initialize(str(component_file), "TestCellNameComponent")
            
            print(f"\n[Sandbox cell_name Test]")
            print(f"  sandbox.cell_name = {sandbox.cell_name}")
            
            assert hasattr(sandbox, "cell_name"), \
                "ComponentSandbox should have cell_name attribute"
            assert sandbox.cell_name == "test_cell_name", \
                f"cell_name should be 'test_cell_name', got '{sandbox.cell_name}'"
            
            key = sandbox.cell_name.lower()
            print(f"  Registry key = {key}")
            
            assert key == "test_cell_name", \
                f"Registry key should be 'test_cell_name', got '{key}'"
            
            print(f"\n  Conclusion: ComponentSandbox.cell_name works correctly for hot reload")
            
        finally:
            ComponentSandbox.remove_sandbox("test_cell_name")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
