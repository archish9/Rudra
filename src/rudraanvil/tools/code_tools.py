"""Code execution and quality tools for agents."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from langchain_core.tools import tool

if TYPE_CHECKING:
    from rudraanvil.filesystem.virtual_fs import VirtualFileSystem


def create_code_tools(vfs: VirtualFileSystem, timeout: int = 30) -> list:
    """Create tools for the agent.
    
    Currently returns an empty list as the agent focuses strictly on code generation
    without testing or code execution in this stage.
    
    Args:
        vfs: The virtual filesystem
        timeout: Maximum execution time in seconds
        
    Returns:
        List of LangChain tools
    """
    
    return []
