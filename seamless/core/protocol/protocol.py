from collections import OrderedDict

"""
A connection declaration may have up to four parts
- io: the type of the connection. Can be "input", "output" or "edit".
  Only pins declare this.
- transfer mode: this declares how the data is transferred.
  - "buffer": the data is transferred as a buffer that can be directly written
    to/from file. It depends on the content type whether the file should be opened
    in text or in binary mode.
  - "copy": the data is transferred as a deep copy that is safe against modifications
    (however, only for edit pins such modifications would be appropriate)
  - "ref": the data is transferred as a reference that should not be modified.
    "ref" merely indicates that a copy is not necessary.
    If transfer-by-reference is not possible for any reason, a copy is transferred instead.
  - "signal": the connection is a signal, no data whatsoever is transferred
- access mode: this declares in which form the data will be accessible to the recipient
  - object: generic Python object (only with "object", "binary" or "mixed" content type)
    Also the format of set_cell
  - pythoncode: a string that can be exec'ed by Python
  - json: the result of json.load, i.e. nested dicts, lists and basic types (str/float/int/bool).
  - silk: a Silk object
  - text: a text string
  TODO: code_object
- content type: the semantic content of the data
  - object: generic Python object
  - text: text
  - python: generic python code
  - transformer: transformer code
  - reactor: reactor code
  - macro: macro code
  - json: JSON data
  - cson: CSON data
  - mixed: seamless.mixed data
  - binary: Numpy data

Note that content types are closely related to (low-level) cell types.
They will never be something as rich as MIME types;
  support for this must be in some high-level annotation/schema field.
"""

transfer_modes = ("buffer", "copy", "ref", "signal")
access_modes = ("object", "pythoncode", "json", "silk", "text") # how the data is accessed
content_types = ("object", "text",
  "python", "transformer", "reactor", "macro",
  "json", "cson", "mixed", "binary"
)
text_types = ("text", "python", "transformer", "reactor", "macro", "cson")


def set_cell(cell, value, *,
  default, from_buffer, force
):
    transfer_mode = "buffer" if from_buffer else "ref"
    different, text_different = cell.deserialize(value, transfer_mode,
      "object", None,
      from_pin=False, default=default,force=force
    )
    return different, text_different

def adapt_cson_json(source):
    assert isinstance(source, str), source
    return cson2json(source)

def adapt_to_silk(source):
    from ...silk import Silk
    return Silk(data=source)

adapters = OrderedDict()
adapters[("copy", "text", "cson"), ("copy", "json", "cson")] = adapt_cson_json
adapters[("copy", "text", "cson"), ("copy", "json", "json")] = adapt_cson_json
for content_type1 in text_types:
    for content_type2 in text_types:
        if content_type1 == content_type2:
            continue
        adapters[("copy", "text", content_type1), ("copy", "text", content_type2)] = True
for content_type in content_types:
    adapters[("copy", "object", content_type), ("copy", "object", "object")] = True
    adapters[("copy", "object", "object"), ("copy", "object", content_type)] = True
adapters[("ref", "object", "json"), ("ref", "object", "mixed")] = True
adapters[("copy", "json", "json"), ("copy", "object", "mixed")] = True
adapters[("copy", "object", "text"), ("copy", "object", "mixed")] = True
adapters[("ref", "json", "json"), ("ref", "silk", "json")] = adapt_to_silk
adapters[("copy", "json", "json"), ("copy", "silk", "json")] = adapt_to_silk
adapters[("copy", "json", "cson"), ("copy", "silk", "cson")] = adapt_to_silk
adapters[("ref", "object", "mixed"), ("ref", "silk", "mixed")] = adapt_to_silk
adapters[("copy", "object", "mixed"), ("copy", "silk", "mixed")] = adapt_to_silk

def select_adapter(transfer_mode, source, target, source_modes, target_modes):
    if transfer_mode == "ref":
        transfer_modes = ["ref", "copy"]
    else:
        transfer_modes = [transfer_mode]
    for trans_mode in transfer_modes:
        for source_mode0 in source_modes:
            if source_mode0[0] != trans_mode:
                continue
            for target_mode in target_modes:
                source_mode = source_mode0
                if target_mode[0] != trans_mode:
                    continue
                if source_mode[1] is None:
                    source_mode = (trans_mode, target_mode[1], source_mode[2])
                if source_mode[2] is None:
                    source_mode = (trans_mode, source_mode[1], target_mode[2])
                if target_mode[1] is None:
                    target_mode = (trans_mode, source_mode[1], target_mode[2])
                if target_mode[2] is None:
                    target_mode = (trans_mode, target_mode[1], source_mode[2])
                if source_mode == target_mode:
                    return None, (source_mode, target_mode)
                adapter = adapters.get((source_mode, target_mode))
                if adapter is not None:
                    if adapter is True:
                        return None, (source_mode, target_mode)
                    else:
                        return adapter, (source_mode, target_mode)
    raise Exception("""Could not find adapter between %s and %s

Supported source modes: %s

Supported target modes: %s

""" % (source, target, source_modes, target_modes))

class TransferredCell:
    is_function = False
    def __init__(self, cell):
        for attr in dir(cell):
            if attr.startswith("_"):
                continue
            setattr(self, attr, getattr(cell, attr))

"""
import inspect, ast
from .cached_compile import cached_compile
from ast import PyCF_ONLY_AST, FunctionDef
class FakeTransformerCell:
    def __init__(self, value):
        if inspect.isfunction(value):
            code = inspect.getsource(value)
            code = strip_source(code)
            value = code
        ast = cached_compile(value, "transformer", "exec", PyCF_ONLY_AST)
        is_function = (len(ast.body) == 1 and
                       isinstance(ast.body[0], FunctionDef))
        if is_function:
            self.func_name = ast.body[0].name
        else:
            self.func_name = "transform"
        self.is_function = is_function
        self.value = value
"""

from .cson import cson2json