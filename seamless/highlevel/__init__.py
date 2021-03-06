import inspect
from copy import deepcopy
from types import LambdaType
from ast import PyCF_ONLY_AST, FunctionDef, Expr, Lambda

from ..mixed import MixedBase
from ..silk import Silk
from ..silk.validation import _allowed_types
from ..core.lambdacode import lambdacode
from ..core.cached_compile import cached_compile

ConstantTypes = _allowed_types + (Silk, MixedBase, tuple)

import inspect
import os
def set_resource(f):
    from .Resource import Resource
    caller_frame = inspect.currentframe().f_back
    filename = inspect.getfile(caller_frame)
    dirname = os.path.dirname(filename)
    ff = os.path.join(dirname, f)
    data = open(ff).read()
    if inspect.getmodule(caller_frame).__name__ == "__main__":
        return Resource(ff, data)
    else:
        return data

def set_hcell(cell, value):
    from ..core.structured_cell import StructuredCellState
    if cell["celltype"] == "structured":
        cell["stored_state"] = StructuredCellState.from_data(value)
    else:
        cell["stored_value"] = value

def parse_function_code(code_or_func, identifier="<None>"):
    if callable(code_or_func):
        func = code_or_func
        code = inspect.getsource(func)
        if isinstance(func, LambdaType) and func.__name__ == "<lambda>":
            code = lambdacode(func)
            if code is None:
                raise ValueError("Cannot extract source code from this lambda")
    else:
        assert isinstance(code_or_func, str)
        code = code_or_func

    ast = cached_compile(code, identifier, "exec", PyCF_ONLY_AST)
    is_function = (len(ast.body) == 1 and
                   isinstance(ast.body[0], FunctionDef))

    if is_function:
        func_name = ast.body[0].name
        code_object = cached_compile(code, identifier, "exec")
    else:
        assert (len(ast.body) == 1 and isinstance(ast.body[0], Expr))
        assert isinstance(ast.body[0].value, Lambda)
        func_name = "<lambda>"
        code_object = cached_compile(code, identifier, "eval")
    return code, func_name, code_object

from .Context import Context
from .Library import stdlib, mylib
from .Reactor import Reactor
from .Transformer import Transformer
from .Cell import Cell
from .SubCell import SubCell
from .Link import Link

from ..lib.compiled_transformer import compiled_transformer as _
from ..lib.bash_transformer import bash_transformer as _

__all__ = ["Context", "stdlib", "mylib", "Reactor", "Transformer", "Link"]
