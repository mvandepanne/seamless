"""
modes and submodes that a *pin* can have, and that must be supported by a cell
These are specific for the low-level.
At the mid-level, the modes would be annotations/hints (i.e. not core),
 and the submodes would be cell languages: JSON, CSON, Silk, Python
"""
modes = ["copy", "ref", "signal"]
submodes = {
    "copy": ["json", "cson", "silk"],
    "ref": ["pythoncode", "json", "silk"]
}

from . import SeamlessBase
from .macro import get_macro_mode
from copy import deepcopy
import hashlib

from ast import PyCF_ONLY_AST, FunctionDef
from .utils import find_return_in_scope
from .cached_compile import cached_compile

transformer_patch = """
import inspect
def {0}():
    global __transformer_frame__
    __transformer_frame__ = inspect.currentframe()
"""

class CellBase(SeamlessBase):
    _exception = None
    _val = None
    _last_checksum = None
    _naming_pattern = "cell"
    _prelim_val = None
    def __init__(self):
        assert get_macro_mode()
        super().__init__()

    def status(self):
        """The cell's current status."""
        return self._status.name

    @property
    def _value(self):
        return self._val

    @_value.setter
    def _value(self, value):
        """Should only ever be set by the manager, since it bypasses validation, last checksum, status flags, etc."""
        self._value = value

    @property
    def value(self):
        return self._val

    def _check_mode(self, mode, submode=None):
        assert mode in modes, mode
        if submode is not None:
            assert submode in submodes[mode], (mode, submodes)

    def touch(self):
        """Forces a cell update, even though the value stays the same
        This triggers all workers that are connected to the cell"""
        manager = self._get_manager()
        manager.touch_cell(self)
        return self

    def set(self, value):
        """Update cell data from Python code in the main thread."""
        if self._context is None:
            self._prelim_val = value
        else:
            manager = self._get_manager()
            manager.set_cell(self, value)
        return self

    def serialize(self, mode, submode=None):
        self._check_mode(mode, submode)
        assert self.status() == "OK", self.status()
        return self._serialize(mode, submode)

    def deserialize(self, value, mode, submode=None):
        """Should normally be invoked by the manager, since it does not notify the manager"""
        self._check_mode(mode, submode)
        if value is None:
            self._val = None
            self._last_checksum = None
            self._status = self.StatusFlags.UNDEFINED
        self._validate(value)
        self._last_checksum = None
        self._deserialize(value, mode, submode)
        self._status = self.StatusFlags.OK
        return self

    @property
    def exception(self):
        """The cell's current exception, as returned by sys.exc_info

        Returns None is there is no exception
        """
        return self._exception

    def _set_exception(self, exception):
        """Should normally be invoked by the manager, since it does not notify the manager"""
        if exception is not None:
            self._status = self.StatusFlags.ERROR
            tb, exc, value = exception #to validate
        self._exception = exception

    def _validate(self, value):
        """Won't raise an exception, but may set .exception"""
        raise NotImplementedError

    def _serialize(self, mode, submode=None):
        raise NotImplementedError

    def _deserialize(self, value, mode, submode=None):
        raise NotImplementedError

    def _checksum(self, value):
        raise NotImplementedError

    def checksum(self):
        assert self.status() == "OK"
        if self._last_checksum is not None:
            return self._last_checksum
        result = self._checksum(self._val)
        self._last_checksum = result
        return result

    def connect(self, target):
        """connects to a target cell"""
        assert get_macro_mode() #or connection overlay mode, TODO
        manager = self._get_manager()
        manager.connect_cell(self, target)

    def as_text(self):
        raise NotImplementedError

class Cell(CellBase):
    """Default class for cells.

Cells contain all the state in text form

Cells can be connected to inputpins, editpins, and other cells.
``cell.connect(pin)`` connects a cell to an inputpin or editpin

Output pins and edit pins can be connected to cells.
``pin.connect(cell)`` connects an outputpin or editpin to a cell

Use ``Cell.value`` to get its value.

Use ``Cell.status()`` to get its status.
"""
    def __init__(self):
        super().__init__()

    def _checksum(self, value):
        return hashlib.md5(str(value).encode("utf-8")).hexdigest()

    def _validate(self, value):
        pass

    def _serialize(self, mode, submode=None):
        if mode == "copy":
            return deepcopy(self._val)
        else:
            return self._val

    def _deserialize(self, value, mode, submode=None):
        self._val = value

    def as_text(self):
        if self._val is None:
            return None
        try:
            return str(self._val)
        except:
            return "<Cannot be rendered as text>"

class PythonCell(Cell):
    """Python code object, used for reactors and macros"""
    _naming_pattern = "pythoncell"

    def _validate(self, value):
        raise NotImplementedError #TODO

    def _serialize(self, mode, submode=None):
        assert mode == "ref" and submode == "pythoncode"
        return self

class PyTransformerCell(PythonCell):
    """Python code object used for transformers (accepts one argument)"""

    def _validate(self, value):
        self.ast = cached_compile(value, "transformer",
                                  "exec", PyCF_ONLY_AST)
        is_function = (len(self.ast.body) == 1 and
                       isinstance(self.ast.body[0], FunctionDef))

        if is_function:
            self.code = cached_compile(value, "transformer",
                                       "exec")
            self.func_name = self.ast.body[0].name
        else:
            self.func_name = "transform"
            try:
                return_node = find_return_in_scope(self.ast)
            except ValueError:
                raise SyntaxError("Block must contain return statement(s)")

            patched_src = transformer_patch.format(self.func_name) + \
              "    " + value.replace("\n", "\n    ").rstrip()
            self.code = cached_compile(patched_src,
                                       "transformer", "exec")

class JsonCell(Cell):
    """A cell in JSON format (monolithic)"""
    #also provides copy+silk and ref+silk transport, but with an empty schema, and text form

    def _checksum(self, value):
        raise NotImplementedError

    def _validate(self, value):
        raise NotImplementedError

    def _serialize(self, mode, submode=None):
        raise NotImplementedError

    def _deserialize(self, value, mode, submode=None):
        raise NotImplementedError

    def as_text(self):
        raise NotImplementedError

class CsonCell(Cell):
    """A cell in CoffeeScript Object Notation (CSON) format
    When necessary, the contents of a CSON cell are automatically converted
    to JSON.
    """

    def _checksum(self, value):
        raise NotImplementedError

    def _validate(self, value):
        raise NotImplementedError

    def _serialize(self, mode, submode=None):
        raise NotImplementedError

    def _deserialize(self, value, mode, submode=None):
        raise NotImplementedError

class Signal(Cell):
    """ A cell that does not contain any data
    When a signal is set, it is propagated as fast as possible:
      - If set from the main thread: immediately. Downstream workers are
         notified and activated (if synchronous) before set() returns
      - If set from another thread: as soon as run_work is called. Then,
         Downstream workers are notified and activated before any other
         non-signal notification.
    """
    _naming_pattern = "signal"

    def _checksum(self, value):
        return None

    def _validate(self, value):
        pass

    def _serialize(self, mode, submode=None):
        raise NotImplementedError

    def _deserialize(self, value, mode, submode=None):
        raise NotImplementedError

def cell(*args, **kwargs):
    return Cell(*args, **kwargs)

def pythoncell(*args, **kwargs):
    return PythonCell(*args, **kwargs)

def pytransformercell(*args, **kwargs):
    return PyTransformerCell(*args, **kwargs)

print("TODO cell: JSON cell")
print("TODO cell: CSON cell")
#...and TODO: cache cell, evaluation cell, event stream
