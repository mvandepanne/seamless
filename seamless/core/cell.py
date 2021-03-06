from copy import deepcopy
import json
import hashlib
from io import BytesIO
import numpy as np
import pickle
import ast
from ast import PyCF_ONLY_AST, FunctionDef, Expr, Lambda
import inspect

from .macro_mode import with_macro_mode
from .protocol import transfer_modes, access_modes, content_types, json_encode
from .. import Wrapper
from . import SeamlessBase
from ..mixed import io as mixed_io
from .cached_compile import cached_compile
from . import macro_register, get_macro_mode
from .mount import MountItem
from .utils import strip_source

cell_counter = 0

class CellLikeBase(SeamlessBase):
    _exported = True
    _is_text = False
    def __init__(self):
        global cell_counter
        super().__init__()
        cell_counter += 1
        self._counter = cell_counter
        if get_macro_mode():
            macro_register.add(self)

    def __hash__(self):
        return self._counter

    def __str__(self):
        ret = "Seamless %s: " % type(self).__name__ + self._format_path()
        return ret


class CellBase(CellLikeBase):
    _has_text_checksum = False
    _exception = None
    _val = None
    _last_checksum = None
    _last_text_checksum = None
    _naming_pattern = "cell"
    _prelim_val = None
    _authoritative = True
    _overruled = False #a non-authoritative cell that has previously received a value
    _mount = None
    _mount_kwargs = None
    _mount_setter = None
    _master = None   #Slave cells. Cannot be written to by API, do not accept connections,
                     #  and mounting is write-only unless there is a mount_setter.
                     # Slave cells are controlled by StructuredCell (the master)
    _lib_path = None # Set by library.libcell
    """
      Sovereignty
      A low level cell may be sovereign if it has a 1:1 correspondence to a mid-level element.
      Sovereign cells are authoritative, they may be changed, and changes to sovereign cells do not cause
      the translation macro to re-trigger.
      When a translation macro is re-triggered for another reason (or when the mid-level is serialized),
      the mid-level element is dynamically read from the sovereign cell (no double representation)
    """
    _sovereign = False
    _observer = None
    _share_callback = None

    def status(self):
        """The cell's current status."""
        return self._status.name

    @property
    def authoritative(self):
        return self._authoritative

    @property
    def _value(self):
        return self._val

    @property
    def _seal(self):
        ctx = self._context
        if ctx is None:
            return None
        return ctx()._seal

    @_value.setter
    def _value(self, value):
        """Should only ever be set by the manager, since it bypasses validation, last checksum, status flags, authority, etc."""
        self._value = value

    @property
    def value(self):
        """Returns the value of the cell
        Usually, this is the same as the data"""
        return self._val

    @property
    def data(self):
        """Returns the cell's data
        cell.set(cell.data) is guaranteed to be a no-op"""
        return self._val

    def touch(self):
        """Forces a cell update, even though the value stays the same
        This triggers all workers that are connected to the cell"""
        manager = self._get_manager()
        manager.touch_cell(self)
        return self

    def set(self, value, force=False):
        """Update cell data from the terminal."""
        if isinstance(value, Wrapper):
            value = value._unwrap()
        if self._context is None:
            self._prelim_val = value, False #non-default-value prelim
        else:
            manager = self._get_manager()
            manager.set_cell(self, value, force=force)
        return self

    def set_default(self, value):
        """Provides a default value for the cell
        This value can be overwritten by workers"""
        if self._context is None:
            self._prelim_val = value, True #default-value prelim
        else:
            manager = self._get_manager()
            manager.set_cell(self, value, default=True)
        return self

    def from_buffer(self, value, checksum=None):
        """Sets a cell from a buffer value"""
        if self._context is None:
            self._prelim_val = value, False #non-default-value prelim
        else:
            manager = self._get_manager()
            manager.set_cell(self, value, from_buffer=True, force=True)
        return self

    def from_file(self, filepath):
        ok = False
        if self._mount_kwargs is not None:
            if "binary" in self._mount_kwargs:
                binary = self._mount_kwargs["binary"]
                if not binary:
                    if "encoding" in self._mount_kwargs:
                        encoding = self._mount_kwargs["encoding"]
                        ok = True
                else:
                    ok = True
        if not ok:
            raise TypeError("Cell %s cannot be loaded from file" % self)
        filemode = "rb" if binary else "r"
        with open(filepath, filemode, encoding=encoding) as f:
            filevalue = f.read()
        self.from_buffer(filevalue)

    def serialize(self, transfer_mode, access_mode, content_type):
        if (transfer_mode, access_mode, content_type) not in self._supported_modes:
            raise TypeError
        return self._serialize(transfer_mode, access_mode, content_type)

    def serialize_buffer(self):
        raise Exception("Cell '%s' cannot be serialized as buffer" % self._format_path())

    def _reset_checksums(self):
        self._last_checksum = None
        self._last_text_checksum = None

    def _assign(self, value):
        assert value is not None
        v = self._val
        if not issubclass(type(value), type(v)):
            self._val = value
            return value
        if isinstance(v, dict):
            v.clear()
            v.update(value)
        elif isinstance(v, list):
            v[:] = value #not for ndarray, since they must have the same shape
        else:
            self._val = value
        return value


    def deserialize(self, value,
      transfer_mode, access_mode, content_type,
      *, from_pin, default, force=False
    ):
        """Should normally be invoked by the manager, since it does not notify the manager
        from_pin: can be True (normal pin that has authority), False (from code) or "edit" (edit pin)
        default: indicates a default value (pins may overwrite it)
        force: force deserialization, even if slave (normally, force is invoked only by structured_cell)
        """        
        assert from_pin in (True, False, "edit", "duplex")
        if not force:
            assert not self._master #slave cells are read-only
        old_status = self._status
        if value is None:
            different = (self._last_checksum is not None)
            text_different = (self._last_text_checksum is not None)
            self._val = None
            self._reset_checksums()
            self._status = self.StatusFlags.UNDEFINED
            return different, text_different
        old_checksum = None
        old_text_checksum = None
        if value is not None:
            if old_status == self.StatusFlags.OK:
                if self.value is not None:
                    old_checksum = self.checksum()
                    old_text_checksum = self.text_checksum()
        self._reset_checksums()
        curr_val = self._val
        try:
            parsed_value = self._deserialize(
              value, transfer_mode, access_mode, content_type
            )
            self._validate(parsed_value)
        except:
            self._val = curr_val
            raise
        self._status = self.StatusFlags.OK
        if old_checksum is None: #old checksum failed
            different = True
            text_different =True
        elif value is not None:
            different = (self.checksum(may_fail=True) != old_checksum)
            self.text_checksum(may_fail=True)
            text_different = (self.text_checksum(may_fail=True) != old_text_checksum)
        else:
            pass #"different" has already been set

        if from_pin == True:
            assert not self._authoritative, self
            self._un_overrule(different)
        elif from_pin == "edit":
            if not self._authoritative:
                if different:
                    self._overrule()
            else:
                self._un_overrule(different)
        elif from_pin == "duplex":
            assert self._authoritative, self
        elif from_pin == False:
            if different and not default and not self._authoritative:
                self._overrule()
            if different and self._seal is not None and not self._sovereign:
                msg = "Warning: setting value for cell %s, controlled by %s"
                print(msg % (self._format_path(), self._seal) )

        if self._observer is not None and self._val is not None:
            self._observer(self._val)
        if self._share_callback is not None:
            self._share_callback()
        return different, text_different

    def _overrule(self):
        if not self._overruled:
            print("Warning: overruling (setting value for non-source cell) %s" % self._format_path())
            self._overruled = True

    def _un_overrule(self, different):
        if self._overruled:
            if different:
                print("Warning: cell %s was formerly overruled, now updated by dependency" % self._format_path())
            self._overruled = False

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
        """Won't raise an exception, but may set .exception (TODO: check???)"""
        raise NotImplementedError

    def _serialize(self, transfer_mode, access_mode, content_type):
        raise NotImplementedError

    def _deserialize(self, value, transfer_mode, access_mode, content_type):
        raise NotImplementedError

    def _checksum(self, value, *, buffer=False, may_fail=False):
        raise NotImplementedError

    def _text_checksum(self, value, *, buffer=False, may_fail=False):
        return self._checksum(value, buffer=buffer, may_fail=may_fail)

    def checksum(self, *, may_fail=False):
        if self.status() != "OK":
            return None
        assert self._val is not None
        if self._last_checksum is not None:
            return self._last_checksum
        result = self._checksum(self._val, may_fail=may_fail)
        self._last_checksum = result
        return result

    def text_checksum(self, *, may_fail=False):
        if not self._has_text_checksum:
            return self.checksum(may_fail=may_fail)
        if self.status() != "OK":
            return None
        assert self._val is not None
        if self._last_text_checksum is not None:
            return self._last_text_checksum
        result = self._text_checksum(self._val, may_fail=may_fail)
        self._last_text_checksum = result
        return result

    @with_macro_mode
    def connect(self, target, transfer_mode=None):
        """connects to a target cell"""
        manager = self._get_manager()
        manager.connect_cell(self, target, transfer_mode=transfer_mode)
        return self

    def as_text(self):
        raise NotImplementedError

    def set_file_extension(self, extension):
        if self._mount is None:
            self._mount = {}
        self._mount.update({"extension": extension})

    def mount(self, path=None, mode="rw", authority="cell", persistent=True):
        """Performs a "lazy mount"; cell is mounted to the file when macro mode ends
        path: file path (can be None if an ancestor context has been mounted)
        mode: "r", "w" or "rw"
        authority: "cell", "file" or "file-strict"
        persistent: whether or not the file persists after the context has been destroyed
        """
        from .mount import is_dummy_mount
        assert is_dummy_mount(self._mount) #Only the mountmanager may modify this further!
        if self._root()._direct_mode:
            raise Exception("Root context must have been constructed in macro mode")
        if self._mount_kwargs is None:
            raise NotImplementedError #cannot mount this type of cell
        kwargs = self._mount_kwargs
        if self._mount is None:
            self._mount = {}
        self._mount.update({
            "autopath": False,
            "path": path,
            "mode": mode,
            "authority": authority,
            "persistent": persistent,
        })
        self._mount.update(self._mount_kwargs)
        MountItem(None, self, dummy=True, **self._mount) #to validate parameters

    def _set_observer(self, observer):
        self._observer = observer
        if self._val is not None:
            observer(self._val)

    def _set_share_callback(self, share_callback):
        self._share_callback = share_callback

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
    _supported_modes = (
        ("ref", "object", "object"),
        ("copy", "object", "object"),
    )

    def _checksum(self, value, *, buffer=False, may_fail=False):
        if value is None:
            return None
        v = str(value)
        if buffer and type(self)._is_text:
            v = v.rstrip("\n")
        return hashlib.md5(v.encode("utf-8")).hexdigest()

    def _validate(self, value):
        pass

    def _serialize(self, transfer_mode, access_mode, content_type):
        if transfer_mode == "buffer":
            raise Exception("Cell '%s' cannot be serialized as buffer" % self._format_path())
        elif transfer_mode == "copy":
            return deepcopy(self._val)
        else:
            return self._val

    def _deserialize(self, value, transfer_mode, access_mode, content_type):
        if transfer_mode == "buffer":
            raise Exception("Cell '%s' cannot be de-serialized as buffer" % self._format_path())
        assert access_mode == "object", (transfer_mode, access_mode)
        return self._assign(value)

    def __str__(self):
        ret = "Seamless cell: " + self._format_path()
        return ret

    def as_text(self):
        if self._val is None:
            return None
        try:
            return str(self._val)
        except:
            return "<Cannot be rendered as text>"

class ArrayCell(Cell):
    """A cell in binary array (Numpy) format"""
    #also provides copy+silk and ref+silk transport, but with an empty schema, and text form

    _mount_kwargs = {"binary": True}

    _supported_modes = []
    for transfer_mode in "buffer", "copy", "ref":
        _supported_modes.append((transfer_mode, "object", "binary"))
    del transfer_mode

    def _checksum(self, value, *, buffer=False, may_fail=False):
        if value is None:
            return None
        if buffer:
            return super()._checksum(value)
        assert isinstance(value, np.ndarray)
        b = self._value_to_bytes(value)
        return super()._checksum(b, buffer=True)

    def _value_to_bytes(self, value):
        b = BytesIO()
        np.save(b, value, allow_pickle=False)
        return b.getvalue()

    def _validate(self, value):
        assert isinstance(value, np.ndarray)

    def serialize_buffer(self):
        return self._value_to_bytes(self._val)

    def _serialize(self, transfer_mode, access_mode, content_type):
        if transfer_mode == "buffer":
            return self.serialize_buffer()
        elif transfer_mode == "copy":
            return deepcopy(self._val)
        else: #ref
            return self._val

    def _from_buffer(self, value):
        if value is None:
            return None
        b = BytesIO(value)
        return np.load(b)

    def _deserialize(self, value, transfer_mode, access_mode, content_type):
        if transfer_mode == "buffer":
            return self._assign(self._from_buffer(value))
        else:
            return self._assign(value)

    def __str__(self):
        ret = "Seamless array cell: " + self._format_path()
        return ret

class MixedCell(Cell):
    _mount_kwargs = {"binary": True}
    _supported_modes = []
    for transfer_mode in "buffer", "copy", "ref":
        _supported_modes.append((transfer_mode, "object", "mixed"))
    del transfer_mode
    _supported_modes = tuple(_supported_modes)

    def __init__(self, storage_cell, form_cell):
        super().__init__()
        self.storage_cell = storage_cell
        self.form_cell = form_cell

    def _from_buffer(self, value):
        if value is None:
            return None
        storage = self.storage_cell.value
        form = self.form_cell.value
        return mixed_io.from_stream(value, storage, form)

    def _value_to_bytes(self, value, storage, form):
        if value is None:
            return None
        return mixed_io.to_stream(value, storage, form)

    def _to_bytes(self):
        storage = self.storage_cell.value
        form = self.form_cell.value
        return self._value_to_bytes(self._val, storage, form)

    def _checksum(self, value, *, buffer=False, may_fail=False):
        if value is None:
            return None
        if buffer:
            b = value
        else:
            #assumes that storage and form are correct!
            storage = self.storage_cell.value
            form = self.form_cell.value
            if may_fail:
                try:
                    b = self._value_to_bytes(value, storage, form)
                except:
                    return None
            else:
                b = self._value_to_bytes(value, storage, form)
        return hashlib.md5(b).hexdigest()

    def _validate(self, value):
        return ###TODO: how to validate?? check that value conforms to form?

    def serialize_buffer(self):
        return self._to_bytes()

    def _serialize(self, transfer_mode, access_mode, content_type):
        if transfer_mode == "buffer":
            return self.serialize_buffer()
        elif transfer_mode == "copy":
            return deepcopy(self._val)
        else: #ref
            return self._val

    def _assign(self, value):
        from seamless.mixed.get_form import get_form
        result = super()._assign(value)
        storage, form = None, None
        if self._val is not None:
            storage, form = get_form(self._val)
        self.storage_cell.set(storage, force=True)
        self.form_cell.set(form, force=True)
        return result

    def _deserialize(self, value, transfer_mode, access_mode, content_type):
        if transfer_mode == "buffer":
            return self._assign(self._from_buffer(value))
        else:
            return self._assign(value)

    def set(self, value, auto_form=False, force=False):
        from seamless.mixed.get_form import get_form
        if auto_form:
            storage, form = get_form(value)
            self.storage_cell.set(storage, force=force)
            self.form_cell.set(form, force=force)
        super().set(value)


    def __str__(self):
        ret = "Seamless mixed cell: " + self._format_path()
        return ret


class TextCell(Cell):
    _is_text = True
    _mount_kwargs = {"encoding": "utf-8", "binary": False}
    _supported_modes = []
    for transfer_mode in "buffer", "copy":
        _supported_modes.append((transfer_mode, "text", "text"))
        _supported_modes.append((transfer_mode, "object", "text"))
    _supported_modes = tuple(_supported_modes)
    del transfer_mode

    def serialize_buffer(self):
        v = self._val
        if v is not None:
            v = v.rstrip("\n")  + "\n"
        return v

    def _serialize(self, transfer_mode, access_mode, content_type):
        if transfer_mode == "buffer":
            return self.serialize_buffer()
        return self._val

    def _deserialize(self, value, transfer_mode, access_mode, content_type):
        v = str(value)
        if transfer_mode == "buffer":
            v = v.rstrip("\n")
        self._val = v
        return v

    def as_text(self):
        if self._val is None:
            return None
        return str(self._val)

    def __str__(self):
        ret = "Seamless text cell: " + self._format_path()
        return ret

class PythonCell(Cell):
    """Generic Python code object"""
    _is_text = True
    _codetype = "func"
    _mount_kwargs = {"encoding": "utf-8", "binary": False}
    is_function = None
    func_name = None

    _supported_modes = []
    for transfer_mode in "buffer", "copy":
        for access_mode in "text", "pythoncode", "object":
            _supported_modes.append((transfer_mode, access_mode, "python"))
    _supported_modes = tuple(_supported_modes)
    del transfer_mode, access_mode

    _naming_pattern = "pythoncell"
    _has_text_checksum = True

    def _text_checksum(self, value, *, buffer=False, may_fail=False):
        v = str(value)
        v = v.rstrip("\n") + "\n"
        return hashlib.md5(v.encode("utf-8")).hexdigest()

    def _checksum(self, value, *, buffer=False, may_fail=False):
        if value is None:
            return None
        if buffer:
            return self._text_checksum(value, buffer=True, may_fail=may_fail)
        tree = ast.parse(value)
        ### TODO: would ast.dump or pickle be more rigorous?
        # For now, use ast, because pickle seems to be newline-sensitive
        dump = ast.dump(tree).encode("utf-8")
        #dump = pickle.dumps(tree)
        return hashlib.md5(dump).hexdigest()

    def _validate(self, value):
        from .protocol import TransferredCell
        if isinstance(value, TransferredCell):
            value = value.data
        if inspect.isfunction(value):
            code = inspect.getsource(value)
            code = strip_source(code)
            value = code
        ast = cached_compile(value, self._codetype, "exec", PyCF_ONLY_AST)
        is_function = (len(ast.body) == 1 and
                       isinstance(ast.body[0], FunctionDef))
        is_expr = (len(ast.body) == 1 and
                       isinstance(ast.body[0], Expr))
        #no multiline expressions, may give indentation syntax errors
        if len(value.splitlines()) > 1:
            is_expr = False

        if is_function:
            self.func_name = ast.body[0].name
        elif is_expr:
            if isinstance(ast.body[0].value, Lambda):
                self.func_name = "<lambda>"
            else:
                self.func_name = "<expr>"
            is_function = True
        else:
            self.func_name = self._codetype

        self.is_function = is_function

    def serialize_buffer(self):
        v = self._val.rstrip("\n")
        return v + "\n"

    def _serialize(self, transfer_mode, access_mode, content_type):
        if access_mode == "text":
            return self._val
        assert access_mode in ("pythoncode", "object")
        if transfer_mode == "buffer":
            return self.serialize_buffer()
        from .protocol import TransferredCell
        return TransferredCell(self)

    def _deserialize(self, value, transfer_mode, access_mode, content_type):
        from .protocol import TransferredCell
        if access_mode in ("object", "pythoncode"):
            # Two possibilities:
            # 1. TransferredCell from pin
            # 2. function object or text object from .set
            if isinstance(value, TransferredCell):
                value = value.data
            else:
                if inspect.isfunction(value):
                    code = inspect.getsource(value)
                    code = strip_source(code)
                    value = code
        v = str(value)
        self._val = v
        return v

    def __str__(self):
        ret = "Seamless Python cell: " + self._format_path()
        return ret

class PyReactorCell(PythonCell):
    """Python code object used for reactors
    a "PINS" object will be inserted into its namespace"""

    _codetype = "reactor"
    _supported_modes = []
    for transfer_mode in "buffer", "copy":
        _supported_modes.append((transfer_mode, "text", _codetype))
        _supported_modes.append((transfer_mode, "object", _codetype))
    _supported_modes.append(("ref", "pythoncode", _codetype))
    _supported_modes = tuple(_supported_modes)
    del transfer_mode

def _validate(self, value):
    super()._validate(value)
    assert self.func_name not in ("<expr>", "<lambda>") #cannot be an expression

class PyTransformerCell(PythonCell):
    """Python code object used for transformers
    Each input will be an argument"""

    _codetype = "transformer"
    _supported_modes = []
    for transfer_mode in "buffer", "copy":
        _supported_modes.append((transfer_mode, "text", _codetype))
        _supported_modes.append((transfer_mode, "object", _codetype))
    _supported_modes.append(("ref", "pythoncode", _codetype))
    _supported_modes = tuple(_supported_modes)
    del transfer_mode

class PyMacroCell(PyTransformerCell):
    """Python code object used for macros
    The context "ctx" will be the first argument.
    Each input will be an argument
    If the macro is a function, ctx must be returned
    """

    _codetype = "transformer"
    _supported_modes = []
    for transfer_mode in "buffer", "copy":
        _supported_modes.append((transfer_mode, "text", _codetype))
        _supported_modes.append((transfer_mode, "object", _codetype))
    _supported_modes.append(("ref", "pythoncode", _codetype))
    _supported_modes = tuple(_supported_modes)
    del transfer_mode

def _validate(self, value):
    super()._validate(value)
    assert self.func_name not in ("<expr>", "<lambda>") #cannot be an expression


class IPythonCell(Cell):
    _is_text = True
    _mount_kwargs = {"encoding": "utf-8", "binary": False}
    _supported_modes = []
    for transfer_mode in "buffer", "copy":
        _supported_modes.append((transfer_mode, "text", "ipython"))
        _supported_modes.append((transfer_mode, "object", "ipython"))
    _supported_modes = tuple(_supported_modes)
    del transfer_mode

    def serialize_buffer(self):
        v = self._val.rstrip("\n")
        return v + "\n"

    def _serialize(self, transfer_mode, access_mode, content_type):
        if transfer_mode == "buffer":
            return self.serialize_buffer()
        return self._val

    def _deserialize(self, value, transfer_mode, access_mode, content_type):
        v = str(value)
        self._val = v
        return v

    def as_text(self):
        if self._val is None:
            return None
        return str(self._val)

    def __str__(self):
        ret = "Seamless IPython cell: " + self._format_path()
        return ret

class JsonCell(Cell):
    """A cell in JSON format (monolithic)"""
    _is_text = True
    _mount_kwargs = {"encoding": "utf-8", "binary": False}

    _supported_modes = []
    for transfer_mode in "buffer", "copy", "ref":
        for access_mode in "json", "text", "object":
            if access_mode == "text" and transfer_mode == "ref":
                continue
            _supported_modes.append((transfer_mode, access_mode, "json"))
    _supported_modes = tuple(_supported_modes)
    del transfer_mode, access_mode

    _naming_pattern = "jsoncell"

    @staticmethod
    def _json(value):
        if value is None:
            return None
        return json_encode(value, sort_keys=True, indent=2)

    def _to_json(self):
        return self._json(self._val)

    def _checksum(self, value, *, buffer=False, may_fail=False):
        if value is None:
            return None
        if buffer:
            return super()._checksum(value)
        j = self._json(value) + "\n"
        return super()._checksum(j)

    def _validate(self, value):
        #TODO: store validation errors
        json_encode(value)

    def serialize_buffer(self):
        v = self._to_json()
        if v is not None:
            v = v.rstrip("\n")
            v += "\n"
        return v

    def _serialize(self, transfer_mode, access_mode, content_type):
        if access_mode == "buffer":
            return self.serialize_buffer()
        if transfer_mode == "copy":
            if access_mode == "text":
                return self._to_json()
            else:
                return deepcopy(self._val)
        else: #ref
            assert access_mode in ("json", "object"), access_mode
            return self._val

    def _from_buffer(self, value):
        return json.loads(value)

    def _deserialize(self, value, transfer_mode, access_mode, content_type):
        if transfer_mode in ("buffer", "text"):
            return self._assign(self._from_buffer(value))
        else:
            return self._assign(value)

    def as_text(self):
        return self._to_json()

    def __str__(self):
        ret = "Seamless JSON cell: " + self._format_path()
        return ret


class CsonCell(JsonCell):
    """A cell in CoffeeScript Object Notation (CSON) format
    When necessary, the contents of a CSON cell are automatically converted
    to JSON.
    """
    _mount_kwargs = {"encoding": "utf-8", "binary": False}
    _supported_modes = []
    for transfer_mode in "buffer", "copy":
        for access_mode in "json", "text", "object":
            _supported_modes.append((transfer_mode, access_mode, "cson"))
    _supported_modes = tuple(_supported_modes)
    del transfer_mode, access_mode

    _naming_pattern = "csoncell"
    _has_text_checksum = True

    def _checksum(self, value, *, buffer=False, may_fail=False):
        if value is None:
            return None
        if buffer:
            return self._text_checksum(value, buffer=True, may_fail=may_fail)
        j = self._json(value)
        return super()._checksum(j)

    def _text_checksum(self, value, *, buffer=False, may_fail=False):
        v = str(value)
        v = v.rstrip("\n") + "\n"
        return hashlib.md5(v.encode("utf-8")).hexdigest()

    @staticmethod
    def _json(value):
        if value is None:
            return None
        d = cson2json(value)
        return json_encode(d, sort_keys=True, indent=2)

    @property
    def value(self):
        """Returns the value of the cell
        For a CSON cell, this is the JSON representation"""
        return cson2json(self._val)

    def _validate(self, value):
        #TODO: store validation errors
        cson2json(value)

    def serialize_buffer(self):
        v = self._val.rstrip("\n")
        return v + "\n"

    def _serialize(self, transfer_mode, access_mode, content_type):
        if access_mode == "json":
            if transfer_mode == "buffer":
                return self._json(self._val)
            else: #copy
                return cson2json(self._val)
        else:
            return self._val

    def _deserialize(self, value, transfer_mode, access_mode, content_type):
        if value is None:
            result = None
        elif access_mode == "json":
            if transfer_mode == "buffer":
                result = value
            else:
                result = json_encode(value, sort_keys=True, indent=2)
        else:
            result = value
        self._val = result
        return result

    def as_text(self):
        if self._val is None:
            return None
        return str(self._val)

    def __str__(self):
        ret = "Seamless CSON cell: " + self._format_path()
        return ret


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

    def _checksum(self, value, *, buffer=False, may_fail=False):
        return None

    def _validate(self, value):
        pass

    def _serialize(self, transfer_mode, access_mode, content_type):
        raise NotImplementedError

    def _deserialize(self, value, transfer_mode, access_mode, content_type):
        raise NotImplementedError

    def __str__(self):
        ret = "Seamless signal: " + self._format_path()
        return ret

celltypes = {
    "text": TextCell,
    "python": PythonCell,
    "ipython": IPythonCell,
    "transformer": PyTransformerCell,
    "reactor": PyReactorCell,
    "macro": PyMacroCell,
    "json": JsonCell,
    "cson": CsonCell,
    "array": ArrayCell,
    "mixed": MixedCell,
    "signal": Signal,
    None: Cell,
}

def cell(celltype=None, **kwargs):
    cellclass = celltypes[celltype]
    return cellclass(**kwargs)

def textcell():
    return TextCell()

def pythoncell():
    return PythonCell()

def pytransformercell():
    return PyTransformerCell()

def pyreactorcell():
    return PyReactorCell()

def pymacrocell():
    return PyMacroCell()

def ipythoncell():
    return IPythonCell()

def jsoncell():
    return JsonCell()

def csoncell():
    return CsonCell()

def arraycell():
    return ArrayCell()

def mixedcell(**kwargs):
    return MixedCell(**kwargs)

def signal():
    return Signal()

extensions = {
    TextCell: ".txt",
    JsonCell: ".json",
    CsonCell: ".cson",
    PythonCell: ".py",
    IPythonCell: ".ipy",
    PyTransformerCell: ".py",
    PyReactorCell: ".py",
    PyMacroCell: ".py",
    IPythonCell: ".ipy",
    MixedCell: ".mixed",
    ArrayCell: ".npy",
}
from ..mixed import MAGIC_SEAMLESS

from ..silk import Silk
if inspect.ismodule(Silk):
    raise ImportError

from .protocol import cson2json

"""
TODO Documentation: only-text changes
     adding comments / breaking up lines to a Python cell will affect a syntax highlighter, but not a transformer, it is only text
     (a refactor that changes variable names would still trigger transformer re-execution, but this is probably the correct thing to do anyway)
     Same for CSON cells: if the CSON is changed but the corresponding JSON stays the same, the checksum stays the same.
     But the text checksum changes, and a text cell or text inputpin will receive an update.
"""
