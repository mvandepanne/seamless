from collections import OrderedDict
import traceback

from .worker import Worker, InputPin, OutputPin
from .cached_compile import cached_compile
from . import cache
from .injector import macro_injector as injector
from . import library
from .protocol import content_types

#TODO: content_type is not used?

class ExecError(Exception): pass

class Macro(Worker):
    active = False
    exception = None
    secondary_exception = None
    macro_tag = "MACRO_"
    injected_modules = None
    def __init__(self, macro_params, *, lib=None):
        self.gen_context = None
        self.macro_context_name = None
        self.code = InputPin(self, "code", "ref", "pythoncode", "transformer")
        self._pins = {"code":self.code}
        self._message_id = 0
        self._macro_params = OrderedDict()
        self._values = {}
        self.code_object = None
        self.namespace = {}
        self.function_expr_template = "{0}\n{1}(ctx=ctx,"
        self.lib = lib
        super().__init__()
        injected_modules = []
        for p in sorted(macro_params.keys()):
            param = macro_params[p]
            self._macro_params[p] = param
            transfer_mode, access_mode, content_type = "copy", None, None
            if isinstance(param, str):
                transfer_mode = param
            elif isinstance(param, (list, tuple)):
                transfer_mode = param[0]
                if len(param) > 1:
                    access_mode = param[1]
                if len(param) > 2:
                    content_type = param[2]
            elif isinstance(param, dict):
                transfer_mode = param.get("transfer_mode", transfer_mode)
                access_mode = param.get("access_mode", access_mode)
                content_type = param.get("content_type", content_type)
            else:
                raise ValueError((p, param))
            if content_type is None and access_mode in content_types:
                content_type = access_mode
            pin = InputPin(self, p, transfer_mode, access_mode)
            if access_mode == "module":
                injected_modules.append(p)
            self.function_expr_template += "%s=%s," % (p, p)
            self._pins[p] = pin
        self.function_expr_template = self.function_expr_template[:-1] + ")"
        self._missing = set(list(macro_params.keys())+ ["code"])
        if len(injected_modules):
            self.injected_modules = injected_modules
            injector.define_workspace(self, injected_modules)

    def __str__(self):
        ret = "Seamless macro: " + self._format_path()
        return ret

    @property
    def ctx(self):
        return self.gen_context

    def execute(self):
        from .macro_mode import macro_mode_on, get_macro_mode
        from .context import context, Context
        assert self._context is not None
        macro_context_name = self.macro_tag + self.name
        self.macro_context_name = macro_context_name
        ctx = None
        mountmanager = self._context()._manager.mountmanager
        outer_macro = (not get_macro_mode())
        try:
            self._pending_updates += 1
            if self.gen_context is not None:
                assert self.gen_context._manager.mountmanager is mountmanager
                self.gen_context._manager.deactivate()
                if outer_macro:
                    old_layers = layer.get_layers(self)
                layer.destroy_layer(self)
            layer.create_layer(self)
            self.exception = 1
            def seal(c):
                c._seal = self
                for child in c._children.values():
                    if isinstance(child, Context):
                        seal(child)
            with mountmanager.reorganize(self.gen_context):
                with macro_mode_on(self):
                    ctx = context(context=self._context(), name=macro_context_name)
                    keep = {k:v for k,v in self.namespace.items() if k.startswith("_")}
                    self.namespace.clear()
                    #self.namespace["__name__"] = self.name
                    self.namespace["__name__"] = "macro"
                    self.namespace.update(keep)
                    self.namespace.update( self.default_namespace.copy())
                    self.namespace["ctx"] = ctx
                    self.namespace.update(self._values)
                    try:
                        workspace = self if self.injected_modules else None
                        with injector.active_workspace(workspace):
                            with library.bind(self.lib):
                                exec(self.code_object, self.namespace)
                        if self.namespace["ctx"] is not ctx:
                            raise Exception("Macro must return ctx")
                    except Exception as e:
                        self.exception = traceback.format_exc()
                        raise ExecError from None
                    self._context()._add_child(macro_context_name, ctx)
                    seal(ctx)
                    filled_objects = layer.fill_objects(ctx, self)
                    manager = ctx._get_manager()
                    manager.set_filled_objects(filled_objects)
                    manager.activate(only_macros=True)
                    self.exception = None
                    '''
                    Caching happens here
                    The old context (gen_context) is deactivated, but the workers have still been running,
                     and sending updates that are accumulated in the work queue (global and manager-buffered)
                    Now it is time to re-assign those worker kernels and cell values (replacing them with dummies)
                     that are cache hits. Never replace whole cells and contexts!
                    TODO: Finally, for all old cells and workers that were cache hits, a successor must be assigned
                    '''
                    if self.gen_context is not None:
                        hits = cache.cache(ctx, self.gen_context)

                    layer.check_async_macro_contexts(ctx, self)

            with macro_mode_on(self):
                seal(ctx) #caching may have changed some seals
            if outer_macro:
                ctx._get_manager().activate(only_macros=False)
            if self.gen_context is not None:
                layer.clear_objects(self.gen_context)
                self.gen_context.self.destroy()
                self.gen_context._manager.flush()
                self.gen_context.full_destroy()
            self.gen_context = ctx
        except Exception as exc:
            if self.exception is not None:
                if self.exception != 1:
                    print(self.exception)
                else:
                    traceback.print_exc()
                    self.exception = traceback.format_exc()
                self.secondary_exception = None
                try:
                    if ctx is not None:
                        ctx.self.destroy()
                        ctx.full_destroy()
                    if self.gen_context is not None:
                        if outer_macro:
                            with macro_mode_on(self):
                                self.gen_context._remount()
                                self._context()._add_child(macro_context_name, self.gen_context)
                            layer.restore_layers(self, old_layers)
                            self.gen_context._manager.activate(only_macros=False)
                        else:
                            pass #no need to restore anything, the outer macro will do that
                            # NOTE: it is illegal (and anyway, quite difficult) to catch an
                            #  exception in the re-execution of macro that is not outer
                except Exception as exc2:
                    traceback.print_exc()
                    self.secondary_exception = traceback.format_exc()
            else:
                # new context was constructed successfully
                # but something went wrong in cleaning up the old context
                # pretend that nothing happened...
                # but store the exception as secondary exception, just in case
                print("macro CLEANUP error"); traceback.print_exc()
                self.gen_context = ctx
                self.secondary_exception = traceback.format_exc()
            if not outer_macro:
                # Must re-raise the exception so that the outer macro will fail properly
                raise
        finally:
            self._pending_updates -= 1


    def receive_update(self, input_pin, value, checksum, access_mode, content_type):
        if value is None:
            self._missing.add(input_pin)
            self._values[input_pin] = None
        else:
            if not self._receive_update_checksum(input_pin, checksum):
                return
            if input_pin == "code":
                code = value.value
                func_name = value.func_name
                identifier = "Seamless macro: " + self._format_path()
                if value.is_function:
                    expr = self.function_expr_template.format(code, value.func_name)
                    self.code_object = cached_compile(expr, identifier, "exec")
                else:
                    self.code_object = cached_compile(code, identifier, "exec")
            else:
                if self.injected_modules and input_pin in self.injected_modules:
                    language = content_type
                    mod = injector.define_module(self, input_pin, language, value)
                    value = mod
                self._values[input_pin] = value
            if input_pin in self._missing:
                self._missing.remove(input_pin)
            if not len(self._missing):
                self.execute()

    def _touch(self):
        if self.status() == self.StatusFlags.OK.name:
            self.execute()

    def __dir__(self):
        return object.__dir__(self) + list(self._pins.keys())

    def status(self):
        """The computation status of the macro
        Returns a dictionary containing the status of all pins that are not OK.
        If all pins are OK, returns the status of the macro itself: OK or pending
        """
        result = {}
        for pinname, pin in self._pins.items():
            s = pin.status()
            if s != self.StatusFlags.OK.name:
                result[pinname] = s
        if len(result):
            return result
        if self.exception is not None:
            return self.StatusFlags.ERROR.name
        return self.StatusFlags.OK.name

    def _unmount(self,from_del=False):
        if self.gen_context is not None:
            return self.gen_context._unmount(from_del)

    def _remount(self):
        if self.gen_context is not None:
            return self.gen_context._remount()

    def destroy(self,from_del=False):
        if self.gen_context is not None:
            return self.gen_context.destroy(from_del)

    def full_destroy(self,from_del=False):
        if self.gen_context is not None:
            return self.gen_context.full_destroy(from_del)

    def equilibrate(self,*args, **kwargs):
        if self.gen_context is not None:
            return self.gen_context.equilibrate(*args, **kwargs)
        else:
            return set()

    def _flush_workqueue(self, full):
        if self.gen_context is not None:
            return self.gen_context._flush_workqueue(full)
        else:
            if not full:
                return True
            else:
                return True, set()

    def activate(self, only_macros):
        super().activate(only_macros)
        if self.gen_context is not None:
            self.gen_context._get_manager().activate(only_macros)

def macro(params, *, lib=None):
    return Macro(params, lib=lib)

from . import cell, transformer, pytransformercell, link, layer, path, \
 reactor, pyreactorcell, pymacrocell, jsoncell, csoncell, mixedcell, \
 arraycell, mixedcell, signal, pythoncell, ipythoncell, libcell, libmixedcell
from .structured_cell import StructuredCell, BufferWrapper
from .context import context
names = ("cell", "transformer", "context", "pytransformercell", "link", "layer",
 "path", "reactor", "pyreactorcell", "pymacrocell", "jsoncell", "csoncell",
 "mixedcell", "arraycell", "signal", "pythoncell", "ipythoncell", "libcell", "libmixedcell")
names += ("StructuredCell", "BufferWrapper")
names = names + ("macro",)
Macro.default_namespace = {n:globals()[n] for n in names}
