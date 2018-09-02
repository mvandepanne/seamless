import inspect

from . import ConstantTypes
from ..mixed import MixedBase
from ..silk import Silk
from .Cell import Cell
from .pin import InputPin, OutputPin
from .Transformer import Transformer
from . import copy_context
import json

def assign_constant(ctx, path, value):
    if isinstance(value, (Silk, MixedBase)):
        raise NotImplementedError
    #TODO: run it through Silk or something, to check that there aren't lists/dicts/tuples-of-whatever-custom-classes
    # not sure if tuple is natively accepted too
    if path in ctx._children:
        old = ctx._children[path]
        if isinstance(old, Cell):
            old.set(value)
            return False
        raise AttributeError(path) #already exists
    Cell(ctx, path) #inserts itself as child
    cell = {
        "path": path,
        "type": "cell",
        "celltype": "structured",
        "format": "mixed",
        "silk": True,
        "buffered": True,
        "value": value,
        "schema": None,
    }
    ### json.dumps(cell)
    ctx._graph[0][path] = cell
    return True

def assign_transformer(ctx, path, func):
    if path in ctx._children:
        old = ctx._children[path]
        if isinstance(old, Cell):
            old.set(func)
            return
        raise AttributeError(path) #already exists
    parameters = list(inspect.signature(func).parameters.keys())
    #TODO: look at default parameters, make them optional
    transformer =    {
        "path": path,
        "type": "transformer",
        "language": "python",
        "code": inspect.getsource(func),
        "pins": {param:{"submode": "silk"} for param in parameters},
        "values": {},
        "RESULT": "result",
        "INPUT": "inp",
        "with_schema": False,
        "buffered": True,
        "plain": False,
        "plain_result": False,
        "in_equilibrium": False,
    }
    ### json.dumps(transformer)
    ctx._graph[0][path] = transformer
    Transformer(ctx, path) #inserts itself as child

def assign_connection(ctx, source, target, standalone_target):
    if standalone_target and target not in ctx._children:
        assign_constant(ctx, target, None)
    assert source in ctx._children, source
    s = ctx._children[source]
    assert isinstance(s, (Cell, OutputPin))
    if s._virtual_path is not None:
        source = s._virtual_path
    if standalone_target:
        t = ctx._children[target]
        assert isinstance(t, (Cell, InputPin))
        if t._virtual_path is not None:
            target = t._virtual_path
    connection = {
        "type": "connection",
        "source": source,
        "target": target
    }
    ctx._graph[1].append(connection)

def assign_context(ctx, path, value):
    new_ctx = value._run_copier()
    new_nodes, new_connections = new_ctx._get_graph()
    copy_context.assign_context(ctx, new_nodes, new_connections, path)
    ctx._translate()

def assign(ctx, path, value):
    from .Context import Context, SubContext
    if isinstance(value, Transformer):
        value._assign_to(ctx, path)
    elif isinstance(value, Cell):
        assert value._parent() is ctx
        assign_connection(ctx, value._path, path, True)
        ctx._translate()
    elif isinstance(value, ConstantTypes):
        new_cell = assign_constant(ctx, path, value)
        if new_cell:
            ctx._translate()
    elif isinstance(value, (Context, SubContext)):
        assign_context(ctx, path, value)
        ctx._translate()
    elif callable(value):
        assign_transformer(ctx, path, value)
        ctx._translate()
        return
    else:
        raise TypeError(value)
    ### g = {".".join(k): v for k,v in ctx._graph[0].items()}
    ### json.dumps([g, ctx._graph[1]])
