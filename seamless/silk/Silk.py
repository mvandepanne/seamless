import inspect, sys, traceback
from types import MethodType
from copy import copy, deepcopy

from .SilkBase import SilkBase, SilkHasForm, compile_function, AlphabeticDict
from .validation import schema_validator, form_validator, Scalar, scalar_conv, _types, infer_type
from .schemawrapper import SchemaWrapper

from .policy import default_policy
SILK_NO_METHODS = 1
SILK_NO_VALIDATION = 2
SILK_BUFFER_CHILD = 4

_underscore_attribute_names =  set(["__array_struct__", "__array_interface__", "__array__"])
# A set of magic names where it is expected that they raise NotImplementedError if
# not implemented, rather than returning NotImplemented
_underscore_attribute_names2 =  set(["__deepcopy__"])
# A set of magic names where it is expected that they raise AttributeError
# not implemented, rather than returning NotImplemented

"""
"stateful" means:
- a state can be obtained using ._get_state and restored using ._set_state
- a value can be set using .set
This applies to both data and buffer!
"""

"""
Buffering:
  Buffered structures have all of their modification written into the buffer, instead of into the data.
  Whenever the buffer is successfully validated (i.e. upon every non-fork modification, or when the fork is joined)
  the buffer is copied into the data. For buffered structures, creating a fork forks the buffer, not the data.
  All data accesses also access the buffer, not the data. To access the data directly, you need to wrap it
  in an unbuffered Silk structure.
"""

class Silk(SilkBase):
    __slots__ = [
            "_parent", "data", "_schema",
            "_modifier", "_forks", "_buffer", "_stateful", "_buffer_nosync"
    ]
    # TODO: append method that may also create a schema, depending on policy.infer_item

    def __init__(self, schema = None, *, parent = None, data = None,
      modifier = 0, buffer = None, stateful = False):
        self._parent = parent
        self._modifier = modifier
        self._forks = []
        self.data = data
        self._buffer = buffer
        self._stateful = stateful
        self._buffer_nosync = False
        assert not isinstance(data, Silk)
        if schema is None:
            schema = {}
        assert isinstance(schema, dict) #for now, no smart wrappers around schema are allowed
                                        # as this complicates forking and external upaates to schema.
                                        # see the note in structured_cell:"schema could become not a slave"
        self._schema = schema

    """
    def __deepcopy__(self, memo):
        data = deepcopy(self.data, memo)
        forks = deepcopy(self._forks, memo)
        schema = deepcopy(self._schema, memo)
        result = type(self)(
          schema, parent=self._parent, data=data, modifier=self._modifier
        )
        result._forks = forks
        return result
    """

    def __call__(self, *args, **kwargs):
        data = self.data
        schema = self._schema
        methods = schema.get("methods", {})
        if data is None:
            constructor_code = methods.get("__init__", None)
            if constructor_code is None:
                raise AttributeError("__init__")
            constructor = compile_function(constructor_code)
            result = constructor(self, *args, **kwargs)
            assert result is None # __init__ must return None
            return self
        else:
            call_code = methods.get("__call__", None)
            if call_code is None:
                raise AttributeError("__call__")
            call = compile_function(call_code)
            return call(self, *args, **kwargs)

    @property
    def parent(self):
        if self._parent is None:
            return AttributeError
        return self._parent

    #***************************************************
    #*  methods for setting
    #***************************************************

    def _set_value_simple(self, value, buffer):
        if buffer:
            if self._stateful:
                self._buffer.set(value)
            else:
                self._buffer = value
        else:
            if self._stateful:
                self.data.set(value)
            else:
                self.data = value

    def _set_value_dict(self, value, buffer):
        data = self.data
        if buffer:
            data = self._buffer
        wdata = data
        if isinstance(wdata, MixedBase): #hackish, but necessary
            wdata = wdata.value
        if isinstance(wdata, Silk):
            wdata = wdata.data
        if not isinstance(wdata, dict):
            if self._stateful:
                data.set(value)
            elif data is None:
                if buffer:
                    self._buffer = value
                else:
                    self.data = value
            else:
                raise TypeError(type(wdata)) #  better be strict for now
        else:
            wdata.clear()
            wdata.update(value)

    def _set(self, value, lowlevel, buffer):
        value_schema = None
        if isinstance(value, Silk):
            value_schema = value.schema.dict
            value = value.data
        is_none = False
        if buffer:
            is_none = (self._buffer is None)
        else:
            is_none = (self.data is None)
        if is_none:
            self._set_value_simple(value, buffer)
            if not lowlevel:
                schema = self._schema
                if (schema is None or schema == {}) and value_schema is not None:
                    if schema is None:
                        schema = value_schema
                        self._schema = schema
                    else:
                        schema.update(value_schema)
                policy = schema.get("policy", None)
                if policy is None or not len(policy):
                    #TODO: implement lookup hierarchy wrapper that also looks at parent
                    policy = default_policy
                if policy["infer_type"]:
                    if "type" not in schema:
                        type_ = infer_type(value)
                        schema["type"] = type_
                if isinstance(value, _types["array"]) and len(self.data) > 0:
                    self._infer_list_item(value_schema)
        elif isinstance(value, Scalar):
            assert self._parent is None #MUST be independent
            self._set_value_simple(value, buffer)
        elif isinstance(value, _types["array"]):
            #invalidates all Silk objects constructed from items
            if buffer:
                if self._stateful:
                    self._buffer.set(value)
                else:
                    self._buffer[:] = value
            else:
                if self._stateful:
                    self.data.set(value)
                else:
                    self.data[:] = value
            if not lowlevel and not self._buffer_nosync:
                empty_list == (len(self.data) == 0)
                if empty_list:
                    self._infer_list_item(value_schema)
        elif isinstance(value, dict):
            #invalidates all Silk objects constructed from items
            self._set_value_dict(value, buffer)

    def set(self, value):
        buffer = (self._buffer is not None)
        self._set(value, lowlevel=False, buffer=buffer)
        if not len(self._forks):
            self.validate()
        return self

    def _setitem(self, attr, value):
        data, schema = self.data, self._schema
        policy = schema.get("policy", None)
        if policy is None or not len(policy):
            #TODO: implement lookup hierarchy wrapper that also looks at parent
            policy = default_policy

        if self._buffer is None and data is None:
            assert self._parent is None # MUST be independent
            data = AlphabeticDict()
            self._set_value_dict(data, buffer=False)

        if self._buffer is not None:
            self._buffer[attr] = value
        else:
            data[attr] = value

        if isinstance(value, Silk):
            value, value_schema = value.data, value._schema
            if "properties" not in schema:
                schema["properties"] = {}
            if attr not in schema["properties"]:
                schema["properties"][attr] = value_schema
                #TODO: infer_property check

        if policy["infer_type"]:
            if "properties" not in schema:
                schema["properties"] = {}
            if attr not in schema["properties"]:
                schema["properties"][attr] = {}
            if "type" not in schema["properties"][attr]:
                type_ = infer_type(value)
                schema["properties"][attr]["type"] = type_

        # TODO: make conditional upon policy.infer_property


    def __setattr__(self, attr, value):
        if attr in type(self).__slots__:
            return super().__setattr__(attr, value)
        if attr in type(self).__dict__ and not attr.startswith("__"):
            raise AttributeError(attr) #Silk method
        if attr == "schema":
            if isinstance(value, SchemaWrapper):
                value = value.dict
            return super().__setattr__(attr, value)
        if isinstance(value, property):
            return self._set_property(attr, value)
        if callable(value):
            return self._set_method(attr, value)

        schema = self._schema
        m = schema.get("methods", {}).get(attr, None)
        if not (self._modifier & SILK_NO_METHODS) and m is not None:
            if m.get("property", False):
                setter = m.get("setter", None)
                if setter is not None:
                    mm = {"code": setter, "language": m["language"]}
                    fset = compile_function(mm)
                    fset(self, value)
                else:
                    raise TypeError(attr) #read-only property cannot be assigned to
            else:
                raise TypeError(attr) #method cannot be assigned to
        else:
            self._setitem(attr, value)
        if not len(self._forks):
            self.validate()

    def __setitem__(self, item, value):
        self._setitem(item, value)
        if not len(self._forks):
            self.validate()

    def _set_property(self, attribute, prop):
        assert (not attribute.startswith("_")) or attribute.startswith("__"), attribute
        assert isinstance(prop, property)
        m = {"property": True, "language": "python"}
        getter_code = inspect.getsource(prop.fget)
        m["getter"] = getter_code
        mm = {"code": getter_code, "language": "python"}
        compile_function(mm, mode="property-getter")
        if prop.fset is not None:
            setter_code = inspect.getsource(prop.fset)
            m["setter"] = setter_code
            mm = {"code": setter_code, "language": "python"}
            compile_function(mm)
        # TODO: deleter

        schema = self._schema
        methods = schema.get("methods", None)
        if methods is None:
            methods = {}
            schema["methods"] = methods
        methods[attribute] = m

    def _set_method(self, attribute, func):
        assert (not attribute.startswith("_")) or attribute.startswith("__"), attribute
        assert callable(func)
        code = inspect.getsource(func)
        m = {"code": code, "language": "python"}
        compile_function(m)

        schema = self._schema
        methods = schema.get("methods", None)
        if methods is None:
            methods = {}
            schema["methods"] = methods
        methods[attribute] = m

    def _add_validator(self, func, attr, *, from_meta):
        assert callable(func)
        code = inspect.getsource(func)
        v = {"code": code, "language": "python"}
        compile_function(v)

        schema = self._schema
        if isinstance(attr, int):
            items_schema = schema.get("items", None)
            if items_schema is None:
                #TODO: check for uniform/pluriform
                items_schema = {}
                schema["items"] = items_schema
            schema = items_schema
        elif isinstance(attr, str):
            prop_schema = schema.get("properties", None)
            if prop_schema is None:
                prop_schema = {}
                schema["properties"] = prop_schema
            attr_schema = prop_schema.get(attr, None)
            if attr_schema is None:
                attr_schema = {}
                prop_schema[attr] = attr_schema
            schema = attr_schema
        validators = schema.get("validators", None)
        if validators is None:
            validators = []
            schema["validators"] = validators
        validators.append(v)

    def add_validator(self, func, attr=None):
        schema = self._schema
        old_validators = copy(schema.get("validators", None))
        ok = False
        try:
            self._add_validator(func, attr, from_meta=False)
            self.validate(full = False)
            ok = True
        finally:
            if not ok:
                schema.pop("validators", None)
                if old_validators is not None:
                    schema["validators"] = old_validators

    def _infer_list_item(self, item_schema):
        schema = self._schema
        policy = schema.get("policy", None)
        if policy is None or not len(policy):
            #TODO: implement lookup hierarchy wrapper that also looks at parent
            policy = default_policy
        infer_item = policy["infer_item"]
        if infer_item != "uniform":
            raise NotImplementedError(infer_item)
            # TODO: if "pluriform", need a new method:
            #   _infer_additional_list_item must be called for each .append
            #   In addition, deletion/[:] can become quite tricky: ignore?
        assert len(self.data) > 0
        if item_schema is None:
            item = self.data[0]
            s = Silk()
            s.item = item
            item_schema = s.schema.properties.item.dict
        schema["items"] = item_schema

    #***************************************************
    #*  methods for getting
    #***************************************************

    def _get_special(self, attr, skip_modify_methods = False):
        if attr in ("validate", "add_validator", "set", "parent", "fork") or \
          (attr.startswith("_") and not attr.startswith("__")):
            return super().__getattribute__(attr)

        if self._buffer is not None:
            self._buffer
            self._schema
            self._modifier
            proxy = Silk(data = self._buffer,
                         schema = self._schema,
                         modifier = self._modifier & SILK_BUFFER_CHILD,
                         parent = self,
                    )
            proxy._forks = self._forks
            return proxy._get_special(attr, skip_modify_methods)

        if not skip_modify_methods:
            is_modifying_method, result = try_modify_methods(self, attr)
            if is_modifying_method:
                return result

        data, schema = self.data, self._schema
        if attr == "self":
            return Silk(data = data,
                        schema = schema,
                        modifier = self._modifier & SILK_NO_METHODS,
                        parent = self._parent
                   )

        if not self._modifier & SILK_NO_METHODS:
            m = schema.get("methods", {}).get(attr, None)
            if m is not None:
                if m.get("property", False):
                    getter = m.get("getter", None)
                    if getter is not None:
                        mm = {"code": getter, "language": m["language"]}
                        fget = compile_function(mm, "property-getter")
                        return fget(self)
                else:
                    method = compile_function(m)
                    return MethodType(method, self)
        if attr in type(self).__slots__:
            return super().__getattribute__(attr)
        data = self.data
        if hasattr(type(data), attr):
            return getattr(data, attr)
        if attr.startswith("__"):
            if attr in _underscore_attribute_names:
                raise NotImplementedError
            elif attr in _underscore_attribute_names2:
                raise AttributeError(attr)
            else:
                return NotImplemented
        raise AttributeError(attr)

    def __getattribute__(self, attr):
        if attr in ("data", "_buffer"):
            return super().__getattribute__(attr)
        if attr == "schema":
            return SchemaWrapper(super().__getattribute__("_schema"))
        try:
            return super().__getattribute__("_get_special")(attr)
        except (TypeError, KeyError, AttributeError, IndexError) as exc:
            if attr.startswith("_"):
                raise AttributeError(attr) from None
            try:
                return super().__getattribute__("_getitem")(attr)
            except (TypeError, KeyError, AttributeError, IndexError):
                raise AttributeError(attr) from None
            except:
                raise exc from None

    def _getitem(self, item):
        data, schema = self.data, self._schema
        modifier = self._modifier
        if self._buffer is not None:
            data = self._buffer
            modifier = modifier & SILK_BUFFER_CHILD
        if isinstance(item, str) and hasattr(data, item):
            return getattr(data, item)
        d = data[item]
        if isinstance(d, Scalar):
            return scalar_conv(d)
        if isinstance(item, slice):
            # TODO: slice "items" schema if it is a list
            return Silk(
                parent=self,
                data=d,
                schema=schema,
                modifier=SILK_NO_VALIDATION & modifier,
            )

        if isinstance(item, int):
            schema_items = schema.get("items", None)
            if schema_items is None:
                schema_items = {}
                schema["items"] = schema_items
            elif isinstance(schema_items, list):
                child_schema = schema_items[item]
        else:
            schema_props = schema.get("properties", None)
            if schema_props is None:
                schema_props = {}
                schema["properties"] = schema_props
            child_schema = schema_props.get(item, None)
            if child_schema is None:
                child_schema = {}
                schema_props[item] = child_schema

        return Silk(
          parent=self,
          data=d,
          schema=child_schema,
          modifier=modifier,
        )

    def __getitem__(self, item):
        if isinstance(item, str):
            try:
                return self._getitem(item)
            except (TypeError, KeyError, AttributeError) as exc:
                try:
                    return self._get_special(item)
                except (TypeError, KeyError, AttributeError) as exc2:
                    raise exc2 from None
                else:
                    raise exc from None
        else:
            return self._getitem(item)



    def _validate(self, full):
        if not self._modifier & SILK_NO_VALIDATION:
            if full:
                if self._buffer is not None:
                    data = self._buffer
                else:
                    data = self.data
                if isinstance(data, MixedBase):
                    # This is hackish, to special-case MixedBase thus
                    # But there is really no alternative, except to add
                    #  MutableXXX bases classes to ./validation.py
                    #  and even then, MixedObject is polymorphic:
                    #   it can be dict/list/scalar/None
                    # Better to validate the underlying value
                    #  (wrapped by MixedBase) instead
                    data = data.value
                if isinstance(self.data, SilkHasForm):
                    form = self.data._get_silk_form()
                    form_validator(self._schema).validate(form)
                schema_validator(self._schema).validate(data)
            else:
                schema = self._schema
                proxy = self
                data = self.data
                if self._buffer is not None:
                    data = self._buffer
                if self._buffer is not None or isinstance(data, MixedBase): #hackish, see above
                    modifier = self._modifier
                    if self._buffer is not None:
                        modifier = modifier & SILK_BUFFER_CHILD
                    if isinstance(data, MixedBase):
                        data = data.value
                    if isinstance(data, Silk):
                        data = data.data
                    proxy = type(self)(
                      schema, parent=self._parent, data=data, modifier=modifier,
                    )
                    proxy._forks = self._forks
                validators = schema.get("validators", [])
                for validator_code in validators:
                    validator_func = compile_function(validator_code)
                    validator_func(proxy)
        if self._parent is not None:
            self.parent.validate(full=False)

    def _commit_buffer(self):
        if self._stateful:
            state = self._buffer._get_state()
            b = self._buffer
            try:
                self._buffer = None
                self.data._set_state(state)
            finally:
                self._buffer = b
        else:
            self._set(deepcopy(self._buffer),lowlevel=True,buffer=False)
        self._buffer_nosync = False

    def validate(self, full=True):
        if (self._modifier & SILK_BUFFER_CHILD) or self._buffer is not None:
            try:
                self._validate(full=full)
                if self._buffer is not None:
                    self._commit_buffer()
            except:
                #TODO: store exception instead
                print("Warning: exception in buffered Silk structure")
                traceback.print_exc(0)
                self._buffer_nosync = True
        else:
            self._validate(full=full)

    def fork(self):
        if self._buffer is not None:
            return _BufferedSilkFork(self)
        else:
            return _SilkFork(self)

class _SilkFork:
    _joined = False
    _stateful = False
    def __init__(self, parent):
        self.parent = parent
         #for now, no smart wrappers around schema are allowed; see above
        assert isinstance(parent._schema, dict), type(parent._schema)
        try:
            state = parent.data._get_state()
            self._stateful = True
            self.data_state = state
        except:
            self.data = deepcopy(parent.data)
        self._schema = deepcopy(parent._schema)
        parent._forks.append(self)

    def _join(self, exception):
        parent = self.parent
        ok = False
        try:
            if exception is None:
                parent.validate()
                ok = True
        finally:
            if not ok:
                if self._stateful:
                    parent.data._set_state(self.data_state)
                else:
                    parent._set(self.data, lowlevel=True, buffer=False)
                parent._schema.clear()
                parent._schema.update(self._schema)
            if len(parent._forks): #could be, because of exception
                parent._forks.pop(-1) #should return self
            self._joined = True

    def join(self):
        self._join(None)

    def __enter__(self):
        yield parent

    def __exit__(self, exc_type, exc_value, traceback):
        self._join(exc_value)

    def __del__(self):
        if self._joined:
            return
        self._join(None)

class _BufferedSilkFork(_SilkFork):
    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        if parent._stateful:
            state = parent._buffer._get_state()
            self._buffer_state = state
        else:
            self._buffer = deepcopy(parent._buffer)

    def _join(self, exception):
        parent = self.parent
        ok = False
        validated = False
        try:
            if exception is None:
                b = parent._buffer
                try:
                    parent._buffer = None
                    parent.validate() #should not affect
                    validated = True
                finally:
                    parent._buffer = b
                parent._commit_buffer()
                ok = True
        finally:
            if not ok:
                if exception is None:
                    if validated: #_commit_buffer went wrong, data may be corrupted
                        if parent._stateful:
                            parent.data._set_state(self.data_state)
                        else:
                            parent._set(self.data, lowlevel=True, buffer=False)
                    if parent._stateful:
                        parent._buffer._set_state(self._buffer_state)
                    else:
                        parent._set(self._buffer, lowlevel=True, buffer=True)
                parent._schema.clear()
                parent._schema.update(self._schema)
            if len(parent._forks): #could be, because of exception
                parent._forks.pop(-1) #should return self
            self._joined = True


from .modify_methods import try_modify_methods
from ..mixed import MixedBase
