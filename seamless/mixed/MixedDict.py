from numpy import ndarray, void
from collections.abc import MutableMapping
from . import MixedBase, is_np_struct
from .get_form import get_form_dict

class MixedDict(MixedBase, MutableMapping):
    def __getitem__(self, item):
        path = self._path + (item,)
        return self._monitor.get_path(path)
    def __setitem__(self, item, value):
        path = self._path + (item,)
        self._monitor.set_path(path, value)
    def __delitem__(self, item):
        path = self._path + (item,)
        self._monitor.del_path(path)
    def __iter__(self):
        data = self._monitor.get_data(self._path)
        return iter(data)
    def __len__(self):
        data = self._monitor.get_data(self._path)
        return len(data)
    def clear(self):
        for path in list(self.keys()):
            if isinstance(path, str):
                path = (path,)
            self._monitor.del_path(path)
    def update(self, other):
        for k,v in other.items():
            path = self._path + (k,)
            self._monitor.set_path(path, v)


class MixedNumpyStruct(MixedBase, MutableMapping):
    def __getitem__(self, item):
        path = self._path + (item,)
        return self._monitor.get_path(path)
    def __setitem__(self, item, value):
        path = self._path + (item,)
        self._monitor.set_path(path, value)
    def __delitem__(self, item):
        raise TypeError("Cannot delete Numpy struct item '%s'" % item)
    def __iter__(self):
        data = self._monitor.get_data(self._path)
        return data.dtype.fields
    def __len__(self):
        data = self._monitor.get_data(self._path)
        return len(data.dtype.fields)

from .Monitor import Monitor

def mixed_dict(data, storage=None, form=None, *, MonitorClass=Monitor, **args):
    if not isinstance(data, MutableMapping) and not is_np_struct(data):
        raise TypeError(type(data))
    if isinstance(data, MixedDict):
        return MixedDict(data._monitor, data._path)
    else:
        if form is None:
            storage, form = get_form_dict(data)
        if not isinstance(storage, (dict, list)):
            if "storage_hook" not in args:
                args["storage_hook"] = lambda v: v
        if not isinstance(form, (dict, list)):
            if "form_hook" not in args:
                args["form_hook"] = lambda v: v
        monitor = MonitorClass(data, storage, form, **args)
        return MixedDict(monitor, ())
